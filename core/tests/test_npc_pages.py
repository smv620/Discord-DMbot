"""An NPC's page and private notes (#2265): the list's 📖 Open button, the page for the server's
people only, what it shows (the players' view and nothing the list does not show), notes that only
their own person ever reads, and what happens to a note when its NPC is merged, rejected or
deleted. Invented names throughout."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx

from dmbot.bot import register_backup_sections
from dmbot.consent import ConsentStore
from dmbot.dm_screen import npc_pages as npg
from dmbot.memory.models import CONFIRMED
from dmbot.memory.recall import RecallService, RosterEntry
from dmbot.memory.store import MemoryStore
from dmbot.web import npc_pages as web
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import QUILLON, SITE, THURSDAY, FakeDiscord, settings

GUILD = THURSDAY.id
CAROL = SimpleNamespace(id=3003, name="Carol", email="carol@example.com")
DAVE = SimpleNamespace(id=4004, name="Dave", email="dave@example.com")  # in another server only


def sections_reading_the_note_tables() -> list[str]:
    """Backup-section modules that mention the private note tables (there must be none)."""
    root = Path(__file__).resolve().parents[1] / "src" / "dmbot"
    allowed = {"schema.py", "npc_pages.py"}
    found = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        mentions = "npc_notes" in text or "npc_roster" in text
        if "def dump" in text and path.name not in allowed and mentions:
            found.append(path.name)
    return found


class WithNpcs(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.store_npc = npg.NpcRosterStore(self.db)
        self.alice = self.session  # DM of "One"
        self.bob = await self.person(BOB)  # DM of "Two", a player in "One"
        self.carol = await self.person(CAROL)
        self.dave = await self.person(DAVE, [QUILLON])

    async def person(self, user: Any, guilds: Any = None) -> sessions.Session:
        token = await sessions.sign_in(self.web, user, guilds or [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def npc(self, name: str = "Brother Aldric", *, note: str = "") -> RosterEntry:
        """A confirmed entry that the list shows, with the DM's own note when given."""
        written = await self.memory.add_entity(
            GUILD, self.one.id, type="character", role="npc", name=name, source="dm",
            status=CONFIRMED, description=note,
        )  # fmt: skip
        return RosterEntry(written.value.id, name, "NPC", False, also_called=("The Monk",))

    async def listed(
        self, *entries: RosterEntry, campaign: str | None = None, now: int = NOW
    ) -> None:
        await self.store_npc.save(GUILD, campaign or self.one.id, entries, now)

    async def code(self, call: Any) -> str:
        with self.assertRaises(RecapError) as err:
            await call
        return err.exception.code

    async def notes(self, who: sessions.Session) -> list[dict[str, Any]]:
        got = await web.my_notes(self.web, who, f"{GUILD}.{self.one.id}")
        return cast(list[dict[str, Any]], got["notes"])


class ThePage(WithNpcs):
    async def test_the_server_sees_the_list_view_and_the_dm_also_the_dms_note(self) -> None:
        aldric = await self.npc(note="Secretly the baron's spy")
        await self.listed(aldric)
        ref = f"{GUILD}.{self.one.id}"
        page = await web.read_page(self.web, self.carol, ref, aldric.id)
        self.assertEqual(
            (page["name"], page["kind"], page["alsoCalled"], page["isGroup"]),
            ("Brother Aldric", "NPC", ["The Monk"], False),
        )
        self.assertIsNone(page["dmNote"])  # a player never gets the DM's note
        self.assertNotIn("spy", json.dumps(page))
        for key in page:  # nothing beyond what the list shows
            self.assertIn(
                key,
                {"id", "name", "kind", "isGroup", "alsoCalled", "groups", "groupsMore", "ties",
                 "tiesMore", "recaps", "myNote", "isDm", "dmNote"},
            )  # fmt: skip
        mine = await web.read_page(self.web, self.alice, ref, aldric.id)
        self.assertEqual((mine["isDm"], mine["dmNote"]), (True, "Secretly the baron's spy"))
        bob = await web.read_page(self.web, self.bob, ref, aldric.id)
        self.assertEqual((bob["isDm"], bob["dmNote"]), (False, None))  # DM of another campaign

    async def test_someone_outside_the_server_is_told_the_page_is_not_for_them(self) -> None:
        aldric = await self.npc()
        await self.listed(aldric)
        ref = f"{GUILD}.{self.one.id}"
        for call in (
            web.read_page(self.web, self.dave, ref, aldric.id),
            web.save_note(self.web, self.dave, ref, aldric.id, "x"),
            web.my_notes(self.web, self.dave, ref),
            web.delete_my_notes(self.web, self.dave, ref),
        ):
            self.assertEqual(await self.code(call), "not_in_server")

    async def test_an_entry_that_is_not_listed_is_not_found(self) -> None:
        listed = await self.npc("Brother Aldric")
        secret = await self.npc("The Hooded One")  # confirmed but never listed (a secret name)
        unconfirmed = await self.memory.add_entity(
            GUILD, self.one.id, type="character", role="npc", name="Maybe", source="scan"
        )
        await self.listed(listed)
        ref = f"{GUILD}.{self.one.id}"
        for entry_id in (secret.id, unconfirmed.value.id, "f" * 32, "not-an-id"):
            self.assertEqual(
                await self.code(web.read_page(self.web, self.carol, ref, entry_id)), "not_found"
            )

    async def test_another_campaigns_entry_is_never_readable_through_this_one(self) -> None:
        theirs = await self.memory.add_entity(
            GUILD, self.two.id, type="character", role="npc", name="Zorgul", source="dm",
            status=CONFIRMED,
        )  # fmt: skip
        gone = RosterEntry(theirs.value.id, "Zorgul", "NPC", False)
        await self.listed(gone, campaign=self.two.id)
        for campaign in (self.one, self.two):
            ref = f"{GUILD}.{campaign.id}"
            got = web.read_page(self.web, self.carol, ref, gone.id)
            if campaign is self.two:
                self.assertEqual((await got)["name"], "Zorgul")
            else:
                self.assertEqual(await self.code(got), "not_found")
        # nor can a note be put there
        self.assertEqual(
            await self.code(
                web.save_note(self.web, self.carol, f"{GUILD}.{self.one.id}", gone.id, "x")
            ),
            "not_found",
        )

    async def test_the_posted_recaps_that_mention_the_name_are_linked(self) -> None:
        aldric = await self.npc("Mira")
        await self.listed(aldric)
        await self.store.update(
            GUILD, self.one.id, 100, summary="Mira drew her blade at Greywater Keep.",
        )  # fmt: skip
        ref = f"{GUILD}.{self.one.id}"
        page = await web.read_page(self.web, self.carol, ref, aldric.id)
        self.assertEqual(page["recaps"], [], "a draft is not shown")
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET status = 'posted' WHERE guild_id = %s AND campaign_id = %s",
                (GUILD, self.one.id),
            )
        page = await web.read_page(self.web, self.carol, ref, aldric.id)
        self.assertEqual([r["title"] for r in page["recaps"]], ["The Silver Lock"])
        self.assertEqual(page["recaps"][0]["ref"], f"{GUILD}.{self.one.id}.100")


class TheNotes(WithNpcs):
    async def test_a_note_is_seen_by_its_own_person_alone_not_even_the_dm(self) -> None:
        aldric = await self.npc()
        await self.listed(aldric)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, aldric.id, "He owes me a favour.")
        await web.save_note(self.web, self.alice, ref, aldric.id, "The DM's own jotting.")
        for who, want in (
            (self.carol, "He owes me a favour."),
            (self.alice, "The DM's own jotting."),
        ):
            page = await web.read_page(self.web, who, ref, aldric.id)
            self.assertEqual(page["myNote"]["body"], want)
        bob = await web.read_page(self.web, self.bob, ref, aldric.id)
        self.assertIsNone(bob["myNote"])
        self.assertEqual(
            [n["body"] for n in await self.notes(self.carol)], ["He owes me a favour."]
        )
        self.assertEqual(await self.notes(self.bob), [])
        # the website's role has no right on the table itself
        async with self.web.user(self.alice.user_id, session=self.alice.id_hash) as conn:
            with self.assertRaises(Exception):  # noqa: B017 (a permission error)
                await conn.execute("SELECT body FROM npc_notes")

    async def test_saving_clearing_and_the_limits(self) -> None:
        aldric = await self.npc()
        await self.listed(aldric)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, aldric.id, "First")
        await web.save_note(self.web, self.carol, ref, aldric.id, "Second\r\nline")
        self.assertEqual([n["body"] for n in await self.notes(self.carol)], ["Second\nline"])
        await web.save_note(self.web, self.carol, ref, aldric.id, "x" * 5000)  # the most
        for bad in ("x" * 5001, "bell\x07", "nul\x00", 5, None):
            self.assertEqual(
                await self.code(web.save_note(self.web, self.carol, ref, aldric.id, bad)),
                "bad_text",
                repr(bad)[:20],
            )
        await web.save_note(self.web, self.carol, ref, aldric.id, "   ")  # empty clears it
        self.assertEqual(await self.notes(self.carol), [])

    async def test_download_and_delete_are_the_persons_own_in_one_campaign(self) -> None:
        a, b = await self.npc("Brother Aldric"), await self.npc("Cerric")
        await self.listed(a, b)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, a.id, "one")
        await web.save_note(self.web, self.carol, ref, b.id, "two")
        await web.save_note(self.web, self.alice, ref, a.id, "not carol's")
        got = await self.notes(self.carol)
        self.assertEqual([(n["name"], n["body"], n["listed"]) for n in got], [
            ("Brother Aldric", "one", True), ("Cerric", "two", True)])  # fmt: skip
        self.assertEqual(await web.delete_my_notes(self.web, self.carol, ref), 2)
        self.assertEqual(await self.notes(self.carol), [])
        self.assertEqual(len(await self.notes(self.alice)), 1)  # nobody else's were touched

    async def export_with_every_section(self) -> dict[str, Any]:
        """A backup made with every section the running bot registers (one shared helper), so
        "no note in it" is proved against what production exports, including the sections that
        write their own rows, and not against two of them."""
        register_backup_sections(
            self.campaigns,
            forget_audio=lambda guild, campaign: None,
            consenting=ConsentStore(self.db).consenting,
        )
        return await self.campaigns.export(GUILD, self.one.id)

    async def test_no_backup_section_reads_the_note_tables(self) -> None:
        self.assertEqual(sections_reading_the_note_tables(), [])

    async def test_a_note_goes_with_the_campaign_and_is_not_in_a_backup(self) -> None:
        aldric = await self.npc()
        await self.listed(aldric)
        await web.save_note(self.web, self.carol, f"{GUILD}.{self.one.id}", aldric.id, "private")
        exported = repr(await self.export_with_every_section())
        self.assertNotIn("private", exported)
        self.assertNotIn("npc_notes", exported)
        self.assertNotIn("npc_roster", exported)
        await self.campaigns.delete(GUILD, self.one.id)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM npc_notes")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)

    async def test_a_note_is_not_in_the_log(self) -> None:
        aldric = await self.npc()
        await self.listed(aldric)
        with self.assertLogs(level="DEBUG") as logs:
            import logging

            logging.getLogger("probe").debug("start")
            await web.save_note(
                self.web, self.carol, f"{GUILD}.{self.one.id}", aldric.id, "a secret thought"
            )
            await self.listed(aldric)
        self.assertNotIn("a secret thought", "\n".join(logs.output))


class TheLifeOfANote(WithNpcs):
    async def test_a_merge_moves_the_notes_to_the_entry_kept(self) -> None:
        keep, gone = await self.npc("Brother Aldric"), await self.npc("Aldric the Monk")
        await self.listed(keep, gone)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, gone.id, "from the merged one")
        await web.save_note(self.web, self.alice, ref, keep.id, "alice on keep")
        await web.save_note(self.web, self.alice, ref, gone.id, "alice on gone")
        await web.save_note(self.web, self.bob, ref, keep.id, "bob on keep")
        await self.memory.merge(
            GUILD, self.one.id, keep.id, gone.id, source="dm", dm_said_same=True
        )
        await self.listed(keep)  # the list no longer shows the merged entry
        by_person = {
            who: {n["id"]: n["body"] for n in await self.notes(s)}
            for who, s in (("carol", self.carol), ("alice", self.alice), ("bob", self.bob))
        }
        self.assertEqual(by_person["carol"], {keep.id: "from the merged one"})
        self.assertEqual(by_person["alice"], {keep.id: "alice on keep\n\nalice on gone"})
        self.assertEqual(by_person["bob"], {keep.id: "bob on keep"})
        self.assertTrue(all(n["listed"] for n in await self.notes(self.carol)))

    async def marks(self, entry_id: str, who: sessions.Session | None = None) -> list[Any]:
        """(entry, person, orphaned_at) of every note, read as the database's owner."""
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT entry_id, user_id, orphaned_at FROM npc_notes WHERE entry_id = %s",
                (entry_id,),
            )
            return [(r["entry_id"], r["user_id"], r["orphaned_at"]) for r in await cur.fetchall()]

    async def test_a_note_already_marked_moves_and_unmarks_when_the_kept_entry_is_listed(
        self,
    ) -> None:
        keep, gone = await self.npc("Brother Aldric"), await self.npc("Aldric the Monk")
        await self.listed(keep, gone)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, gone.id, "from the merged one")
        await self.listed(keep, now=NOW + 10)  # the merged entry fell off the list: marked
        self.assertEqual([m[2] for m in await self.marks(gone.id)], [NOW + 10])
        await self.memory.merge(
            GUILD, self.one.id, keep.id, gone.id, source="dm", dm_said_same=True
        )
        self.assertEqual(await self.marks(gone.id), [])
        (moved,) = await self.marks(keep.id)
        self.assertIsNone(moved[2])  # the kept entry is listed: the note is live again
        self.assertEqual([n["body"] for n in await self.notes(self.carol)], ["from the merged one"])

    async def test_a_marked_note_keeps_its_clock_when_the_kept_entry_is_not_listed(self) -> None:
        keep, gone = await self.npc("Brother Aldric"), await self.npc("Aldric the Monk")
        other = await self.npc("Someone Else")
        await self.listed(gone, other)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, gone.id, "from the merged one")
        await self.listed(other, now=NOW + 10)  # marked at NOW + 10
        await self.memory.merge(
            GUILD, self.one.id, keep.id, gone.id, source="dm", dm_said_same=True
        )
        (moved,) = await self.marks(keep.id)
        self.assertEqual(moved[2], NOW + 10)  # the merge did not restart its 30 days
        day = 86400
        self.assertEqual(await self.store_npc.sweep([GUILD], NOW + 10 + 30 * day), 1)

    async def test_a_marked_note_joins_a_live_one_and_the_joined_note_is_live(self) -> None:
        keep, gone = await self.npc("Brother Aldric"), await self.npc("Aldric the Monk")
        await self.listed(keep, gone)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.alice, ref, keep.id, "alice on keep")
        await web.save_note(self.web, self.alice, ref, gone.id, "alice on gone")
        await self.listed(keep, now=NOW + 10)
        await self.memory.merge(
            GUILD, self.one.id, keep.id, gone.id, source="dm", dm_said_same=True
        )
        (joined,) = await self.notes(self.alice)
        self.assertEqual(
            (joined["body"], joined["listed"]), ("alice on keep\n\nalice on gone", True)
        )

    async def merged_lengths(
        self, kept: int, merged: int
    ) -> tuple[list[Any], list[Any], list[Any]]:
        """Carol's notes after a merge, having written `kept` and `merged` characters: the kept
        entry's rows, the merged entry's rows, and her notes as the website lists them."""
        keep, gone = await self.npc("Brother Aldric"), await self.npc("Aldric the Monk")
        await self.listed(keep, gone)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, keep.id, "k" * kept)
        await web.save_note(self.web, self.carol, ref, gone.id, "g" * merged)
        await self.memory.merge(
            GUILD, self.one.id, keep.id, gone.id, source="dm", dm_said_same=True
        )
        return (
            await self.marks(keep.id),
            await self.marks(gone.id),
            await self.notes(self.carol),
        )

    async def test_two_notes_of_two_thousand_join_into_one_with_no_duplicate(self) -> None:
        kept_rows, gone_rows, notes = await self.merged_lengths(2000, 2000)
        self.assertEqual((len(kept_rows), gone_rows), (1, []))  # the merged note is not left over
        (joined,) = notes
        self.assertEqual(joined["body"], "k" * 2000 + "\n\n" + "g" * 2000)

    async def test_the_join_fits_at_exactly_five_thousand_characters(self) -> None:
        kept_rows, gone_rows, notes = await self.merged_lengths(2499, 2499)  # 2499 + 2 + 2499
        self.assertEqual((len(kept_rows), gone_rows), (1, []))
        self.assertEqual(len(notes[0]["body"]), 5000)

    async def test_one_character_more_and_the_merged_note_stays_apart(self) -> None:
        kept_rows, gone_rows, notes = await self.merged_lengths(2499, 2500)  # 5001 joined
        self.assertEqual((len(kept_rows), len(gone_rows)), (1, 1))
        self.assertEqual(sorted(len(n["body"]) for n in notes), [2499, 2500])

    async def test_two_marked_notes_that_join_keep_the_older_clock(self) -> None:
        keep, gone = await self.npc("Brother Aldric"), await self.npc("Aldric the Monk")
        other = await self.npc("Someone Else")
        await self.listed(keep, gone, other)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, keep.id, "on keep")
        await web.save_note(self.web, self.carol, ref, gone.id, "on gone")
        await self.listed(gone, other, now=NOW + 5)  # the kept entry's note is marked at NOW + 5
        await self.listed(other, now=NOW + 10)  # the merged entry's, later
        await self.memory.merge(
            GUILD, self.one.id, keep.id, gone.id, source="dm", dm_said_same=True
        )
        (joined,) = await self.marks(keep.id)
        self.assertEqual(joined[2], NOW + 5)  # neither restarted nor made later
        self.assertEqual(await self.marks(gone.id), [])

    async def test_over_the_limit_a_marked_note_stays_apart_and_its_clock_is_not_restarted(
        self,
    ) -> None:
        keep, gone = await self.npc("Brother Aldric"), await self.npc("Aldric the Monk")
        await self.listed(keep, gone)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, keep.id, "k" * 3000)
        await web.save_note(self.web, self.carol, ref, gone.id, "g" * 3000)
        await self.listed(keep, now=NOW + 10)  # the merged entry's note is marked at NOW + 10
        await self.memory.merge(
            GUILD, self.one.id, keep.id, gone.id, source="dm", dm_said_same=True
        )
        (apart,) = await self.marks(gone.id)
        self.assertEqual(apart[2], NOW + 10)  # not the time of the merge
        (kept,) = await self.marks(keep.id)
        self.assertIsNone(kept[2])

    async def test_nobody_but_the_writer_sees_a_moved_note(self) -> None:
        keep, gone = await self.npc("Brother Aldric"), await self.npc("Aldric the Monk")
        await self.listed(keep, gone)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.alice, ref, gone.id, "alice only")
        await self.listed(keep, now=NOW + 10)
        await self.memory.merge(
            GUILD, self.one.id, keep.id, gone.id, source="dm", dm_said_same=True
        )
        self.assertEqual(await self.notes(self.carol), [])
        self.assertEqual(await self.notes(self.bob), [])
        page = await web.read_page(self.web, self.carol, ref, keep.id)
        self.assertIsNone(page.get("myNote"))
        self.assertNotIn("alice only", str(page))

    async def test_two_long_notes_are_not_cut_by_a_merge_the_second_is_kept_apart(self) -> None:
        keep, gone = await self.npc("Brother Aldric"), await self.npc("Aldric the Monk")
        await self.listed(keep, gone)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, keep.id, "k" * 3000)
        await web.save_note(self.web, self.carol, ref, gone.id, "g" * 3000)
        await self.memory.merge(
            GUILD, self.one.id, keep.id, gone.id, source="dm", dm_said_same=True
        )
        await self.listed(keep)
        notes = {n["id"]: n for n in await self.notes(self.carol)}
        self.assertEqual(notes[keep.id]["body"], "k" * 3000)  # unchanged
        self.assertEqual((notes[gone.id]["body"], notes[gone.id]["listed"]), ("g" * 3000, False))

    async def test_a_rejected_name_keeps_the_note_thirty_days_then_it_goes(self) -> None:
        aldric = await self.npc()
        await self.listed(aldric)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, aldric.id, "remember this")
        await self.listed(now=NOW + 10)  # no longer listed
        (kept,) = await self.notes(self.carol)
        self.assertEqual(
            (kept["body"], kept["listed"], kept["name"]), ("remember this", False, "Brother Aldric")
        )
        self.assertEqual(
            await self.code(web.read_page(self.web, self.carol, ref, aldric.id)), "not_found"
        )
        self.assertEqual(
            await self.code(web.save_note(self.web, self.carol, ref, aldric.id, "more")),
            "not_found",
        )
        day = 86400
        self.assertEqual(await self.store_npc.sweep([GUILD], NOW + 10 + 29 * day), 0)
        self.assertEqual(len(await self.notes(self.carol)), 1)
        self.assertEqual(await self.store_npc.sweep([GUILD], NOW + 10 + 30 * day), 1)
        self.assertEqual(await self.notes(self.carol), [])

    async def test_a_name_listed_again_is_unmarked_and_a_rename_follows(self) -> None:
        aldric = await self.npc()
        await self.listed(aldric)
        ref = f"{GUILD}.{self.one.id}"
        await web.save_note(self.web, self.carol, ref, aldric.id, "n")
        await self.listed(now=NOW + 5)
        await self.listed(RosterEntry(aldric.id, "Aldric the Wise", "NPC", False), now=NOW + 9)
        (note,) = await self.notes(self.carol)
        self.assertEqual((note["listed"], note["name"]), (True, "Aldric the Wise"))
        self.assertEqual(await self.store_npc.sweep([GUILD], NOW + 10 * 86400 * 30), 0)

    async def test_the_sweep_is_per_server_and_only_marked_notes(self) -> None:
        aldric = await self.npc()
        await self.listed(aldric)
        await web.save_note(self.web, self.carol, f"{GUILD}.{self.one.id}", aldric.id, "n")
        self.assertEqual(await self.store_npc.sweep([GUILD], NOW + 400 * 86400), 0)  # still listed
        self.assertEqual(await self.store_npc.sweep([999], NOW + 400 * 86400), 0)  # another server


class TheRoutes(WithNpcs):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: NOW,
        )  # fmt: skip
        self.clients: dict[str, httpx.AsyncClient] = {}
        for name, user, guilds in (
            ("carol", CAROL, [THURSDAY]),
            ("dave", DAVE, [QUILLON]),
        ):
            token = await sessions.sign_in(self.web, cast(Any, user), guilds, now=NOW, days=30)
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://web-api.internal.test",
                headers=HEADERS,
            )
            client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
            self.clients[name] = client

    async def asyncTearDown(self) -> None:
        for client in self.clients.values():
            await client.aclose()
        await super().asyncTearDown()

    async def test_the_page_the_note_and_the_download_over_http(self) -> None:
        aldric = await self.npc()
        await self.listed(aldric)
        base = f"/me/campaigns/{GUILD}.{self.one.id}/npcs/{aldric.id}"
        carol, dave = self.clients["carol"], self.clients["dave"]
        got = await carol.get(base)
        self.assertEqual((got.status_code, got.json()["name"]), (200, "Brother Aldric"))
        self.assertEqual((await dave.get(base)).status_code, 403)
        self.assertEqual((await dave.get(base)).json()["error"], "not_in_server")
        self.assertEqual((await carol.get(base.replace(aldric.id, "e" * 32))).status_code, 404)
        put = await carol.put(base + "/note", json={"body": "my words"})
        self.assertEqual((put.status_code, put.json()), (200, {"ok": True}))
        self.assertEqual((await carol.get(base)).json()["myNote"]["body"], "my words")
        self.assertEqual(
            (await carol.put(base + "/note", json={"body": "x" * 5001})).status_code, 422
        )
        self.assertEqual((await dave.put(base + "/note", json={"body": "x"})).status_code, 403)
        mine = await carol.get(f"/me/npc-notes?campaign={GUILD}.{self.one.id}")
        self.assertEqual(mine.status_code, 200)
        self.assertIn("attachment", mine.headers["content-disposition"])
        self.assertEqual(mine.json()["notes"][0]["body"], "my words")
        gone = await carol.delete(f"/me/npc-notes?campaign={GUILD}.{self.one.id}")
        self.assertEqual(gone.json(), {"deleted": 1})
        self.assertEqual(
            (await dave.delete(f"/me/npc-notes?campaign={GUILD}.{self.one.id}")).status_code, 403
        )

    async def test_a_burst_of_saves_is_slowed(self) -> None:
        aldric = await self.npc()
        await self.listed(aldric)
        base = f"/me/campaigns/{GUILD}.{self.one.id}/npcs/{aldric.id}/note"
        codes = {
            (await self.clients["carol"].put(base, json={"body": "x"})).status_code
            for _ in range(70)
        }
        self.assertIn(429, codes)


class ThePass(WithNpcs):
    """The pass that writes the pages' view without a Discord channel (#2874). What matters most:
    people's private notes must not start their 30-day deletion because the channel went away."""

    async def run_pass(self, now: int = NOW) -> int:
        return await npg.refresh(
            RecallService(self.memory), self.store_npc, GUILD, self.one.id, now
        )

    async def orphaned_notes(self) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM npc_notes WHERE orphaned_at IS NOT NULL"
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    async def test_a_confirmed_npc_keeps_its_page_and_notes_with_no_channel_at_all(self) -> None:
        aldric = await self.npc()
        self.assertEqual(await self.run_pass(), 1)
        ref = f"{GUILD}.{self.one.id}"
        page = await web.read_page(self.web, self.carol, ref, aldric.id)
        self.assertEqual(page["name"], "Brother Aldric")
        await web.save_note(self.web, self.carol, ref, aldric.id, "He owes me a favour.")
        day = 86400
        for later in (60, 3600, 40 * day):  # the pass runs again and again; no channel exists
            await self.run_pass(NOW + later)
            self.assertEqual(await self.orphaned_notes(), 0)  # never "no longer listed"
        self.assertEqual(await self.store_npc.sweep([GUILD], NOW + 400 * day), 0)  # not deleted
        (note,) = await self.notes(self.carol)
        self.assertEqual((note["body"], note["listed"]), ("He owes me a favour.", True))
        page = await web.read_page(self.web, self.carol, ref, aldric.id)  # and the page stays
        self.assertEqual(page["myNote"]["body"], "He owes me a favour.")

    async def test_a_new_confirmed_npc_gets_a_page_at_the_next_pass(self) -> None:
        await self.run_pass()
        later = await self.npc("Mira Dawnwhisper")
        ref = f"{GUILD}.{self.one.id}"
        self.assertEqual(
            await self.code(web.read_page(self.web, self.carol, ref, later.id)), "not_found"
        )
        self.assertEqual(await self.run_pass(NOW + 60), 1)
        page = await web.read_page(self.web, self.carol, ref, later.id)
        self.assertEqual(page["name"], "Mira Dawnwhisper")

    async def test_an_entry_the_players_may_no_longer_see_is_marked_as_before(self) -> None:
        aldric = await self.npc()
        await self.run_pass()
        await web.save_note(self.web, self.carol, f"{GUILD}.{self.one.id}", aldric.id, "n")
        await self.memory.set_entity_status(GUILD, self.one.id, aldric.id, "rejected", source="dm")
        await self.run_pass(NOW + 60)
        self.assertEqual(await self.orphaned_notes(), 1)  # the rule itself is unchanged

    async def test_the_bot_writes_the_view_for_a_campaign_that_never_had_a_channel(self) -> None:
        from dmbot.bot import DMBot

        aldric = await self.npc()
        bot: Any = SimpleNamespace(
            memory=self.memory,
            _npcs_roster=self.store_npc,
            _npcs_last={},
            _npcs_service=lambda: RecallService(self.memory),
        )
        await DMBot._refresh_npc_pages(bot, GUILD, self.one.id)
        page = await web.read_page(self.web, self.carol, f"{GUILD}.{self.one.id}", aldric.id)
        self.assertEqual(page["name"], "Brother Aldric")
        self.assertIn(self.one.id, bot._npcs_last)

    def test_passes_are_at_least_a_minute_apart_after_a_short_settle(self) -> None:
        self.assertEqual(npg.pass_delay(None, 100.0), npg.SETTLE_S)
        self.assertEqual(npg.pass_delay(100.0, 110.0), 50.0)
        self.assertEqual(npg.pass_delay(100.0, 500.0), npg.SETTLE_S)
