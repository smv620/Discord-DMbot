"""A backup loaded from the website (#2745): what the API checks from the file itself, who may stage
and read one, the parts, the progress shape, the limits, and that the bytes stay out of reach of
the website's own role. (The bot's half, applying it, is its own tests.)"""

from __future__ import annotations

import gzip
import json
from typing import Any

import httpx

from dmbot import progress
from dmbot.campaigns.parts import PART_READ_BYTES
from dmbot.campaigns.store import MAX_BACKUP_BYTES
from dmbot.web import restores, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

OTHER_SERVER = 777  # a server Alice's session does not have


def a_copy(name: str = "Lost World", parts: int = 0) -> bytes:
    """A small DMbot backup file: only the shape the website looks at (the name and how many
    transcripts files it came with); the bot judges the rest."""
    body: dict[str, Any] = {"campaign": {"name": name}}
    if parts:
        body["parts"] = {"count": parts}
    return gzip.compress(json.dumps(body).encode())


A_PART = gzip.compress(b"{}")


class Staging(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bob = await self.signed(BOB, THURSDAY)

    async def signed(self, user: Any, *guilds: Any) -> Any:
        token = await sessions.sign_in(self.web, user, list(guilds), now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def refused(self, awaited: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await awaited
        return str(caught.exception.code)

    async def db_sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(statement, args)

    def stage(self, raw: bytes, *, session: Any = None, replace: str | None = None) -> Any:
        return restores.stage(self.web, session or self.session, THURSDAY.id, replace, raw)

    # ---- staging the copy ----

    async def test_a_copy_with_no_transcripts_files_goes_straight_to_the_bot(self) -> None:
        made = await self.stage(a_copy())
        self.assertEqual(
            (made["name"], made["partsWanted"], made["state"]), ("Lost World", 0, "waiting")
        )
        got = await restores.status(self.web, self.session, THURSDAY.id, made["id"])
        self.assertEqual((got["stage"], got["state"]), ("restore_waiting", "running"))
        self.assertFalse(got["replacing"])

    async def test_a_copy_with_transcripts_files_waits_for_them(self) -> None:
        made = await self.stage(a_copy(parts=3))
        self.assertEqual((made["partsWanted"], made["state"]), (3, "receiving"))
        got = await restores.status(self.web, self.session, THURSDAY.id, made["id"])
        self.assertEqual(
            (got["stage"], got["partsWanted"], got["partsReceived"]), ("restore_receiving", 3, 0)
        )

    async def test_what_is_not_a_backup_is_refused_before_anything_is_stored(self) -> None:
        for raw in (
            b"",
            b"not a backup",
            gzip.compress(b"[1, 2]"),
            gzip.compress(b'{"campaign": {}}'),
        ):
            self.assertEqual(await self.refused(self.stage(raw)), "not_a_backup")
        self.assertEqual(await self.refused(self.stage(b"x" * (MAX_BACKUP_BYTES + 1))), "too_big")
        self.assertEqual(
            await self.refused(restores.status(self.web, self.session, THURSDAY.id, None)),
            "not_found",
        )

    async def test_a_copy_that_asks_for_more_than_discords_boxes_is_refused(self) -> None:
        self.assertEqual(await self.refused(self.stage(a_copy(parts=25))), "too_many_parts")
        self.assertEqual((await self.stage(a_copy(parts=24)))["partsWanted"], 24)

    async def test_the_limits_are_discords_own(self) -> None:
        self.assertEqual(restores.MAX_PARTS, 24)
        self.assertEqual(restores.MAX_BODY_BYTES, max(MAX_BACKUP_BYTES, PART_READ_BYTES))

    async def test_a_second_restore_while_one_is_pending_says_which(self) -> None:
        first = await self.stage(a_copy())
        with self.assertRaises(RecapError) as caught:
            await self.stage(a_copy("Another"))
        self.assertEqual(caught.exception.code, "busy")
        assert caught.exception.problems is not None
        self.assertEqual(caught.exception.problems[0]["id"], first["id"])

    async def test_a_server_the_session_does_not_have_gets_nothing(self) -> None:
        self.assertEqual(
            await self.refused(
                restores.stage(self.web, self.session, OTHER_SERVER, None, a_copy())
            ),
            "not_found",
        )

    async def test_five_in_an_hour_is_too_soon(self) -> None:
        for _ in range(5):
            await self.stage(a_copy())
            await self.db_sql("UPDATE web_restores SET state = 'failed', stage = 'failed'")
        self.assertEqual(await self.refused(self.stage(a_copy())), "too_soon")

    async def test_a_restore_older_than_a_day_is_swept_with_its_files(self) -> None:
        old = await self.stage(a_copy(parts=1))
        await restores.add_part(self.web, self.session, THURSDAY.id, old["id"], 1, A_PART)
        await self.db_sql("UPDATE web_restores SET created_at = created_at - 90000")
        await self.stage(a_copy("New"))
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT (SELECT count(*) FROM web_restores) AS r,"
                " (SELECT count(*) FROM web_restore_parts) AS p"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["r"], row["p"]), (1, 0))

    # ---- replacing a campaign ----

    async def test_a_campaign_of_the_persons_own_may_be_replaced(self) -> None:
        made = await self.stage(a_copy(), replace=f"{THURSDAY.id}.{self.one.id}")
        got = await restores.status(self.web, self.session, THURSDAY.id, made["id"])
        self.assertTrue(got["replacing"])

    async def test_someone_elses_campaign_cannot_be_named_to_replace(self) -> None:
        self.assertEqual(
            await self.refused(self.stage(a_copy(), replace=f"{THURSDAY.id}.{self.two.id}")),
            "not_found",
        )

    async def test_a_campaign_of_another_server_cannot_be_named_to_replace(self) -> None:
        self.assertEqual(
            await self.refused(self.stage(a_copy(), replace=f"{OTHER_SERVER}.{self.one.id}")),
            "not_found",
        )
        self.assertEqual(await self.refused(self.stage(a_copy(), replace="nonsense")), "not_found")

    # ---- the transcripts files ----

    async def test_the_files_arrive_one_by_one_and_all_are_needed(self) -> None:
        made = await self.stage(a_copy(parts=2))
        rid = made["id"]
        one = await restores.add_part(self.web, self.session, THURSDAY.id, rid, 1, A_PART)
        self.assertEqual(one["received"], 1)
        again = await restores.add_part(self.web, self.session, THURSDAY.id, rid, 1, A_PART)
        self.assertEqual(again["received"], 1)  # the same number replaces
        self.assertEqual(
            await self.refused(
                restores.go(self.web, self.session, THURSDAY.id, rid, without=False)
            ),
            "missing",
        )
        await restores.add_part(self.web, self.session, THURSDAY.id, rid, 2, A_PART)
        await restores.go(self.web, self.session, THURSDAY.id, rid, without=False)
        got = await restores.status(self.web, self.session, THURSDAY.id, rid)
        self.assertEqual((got["stage"], got["partsReceived"]), ("restore_waiting", 2))
        self.assertEqual(
            await self.refused(
                restores.add_part(self.web, self.session, THURSDAY.id, rid, 1, A_PART)
            ),
            "not_receiving",
        )

    async def test_restoring_without_the_files_drops_any_that_came(self) -> None:
        made = await self.stage(a_copy(parts=2))
        rid = made["id"]
        await restores.add_part(self.web, self.session, THURSDAY.id, rid, 1, A_PART)
        await restores.go(self.web, self.session, THURSDAY.id, rid, without=True)
        got = await restores.status(self.web, self.session, THURSDAY.id, rid)
        self.assertEqual(
            (got["partsReceived"], got["withoutParts"], got["stage"]), (0, True, "restore_waiting")
        )

    async def test_a_bad_file_or_number_is_refused(self) -> None:
        made = await self.stage(a_copy(parts=2))
        rid = made["id"]
        add = restores.add_part
        self.assertEqual(
            await self.refused(add(self.web, self.session, THURSDAY.id, rid, 3, A_PART)), "bad_part"
        )
        self.assertEqual(
            await self.refused(add(self.web, self.session, THURSDAY.id, rid, 0, A_PART)), "bad_part"
        )
        self.assertEqual(
            await self.refused(add(self.web, self.session, THURSDAY.id, rid, 1, b"plain")),
            "not_a_backup",
        )
        self.assertEqual(
            await self.refused(add(self.web, self.session, THURSDAY.id, rid, 1, b"")),
            "not_a_backup",
        )
        big = gzip.compress(b"x") + b"\0" * PART_READ_BYTES
        self.assertEqual(
            await self.refused(add(self.web, self.session, THURSDAY.id, rid, 1, big)), "too_big"
        )
        self.assertEqual(
            await self.refused(add(self.web, self.session, THURSDAY.id, "nope", 1, A_PART)),
            "not_found",
        )

    # ---- who can see it ----

    async def test_nobody_else_can_read_add_to_send_or_cancel_it(self) -> None:
        made = await self.stage(a_copy(parts=1))
        rid = made["id"]
        bob = self.bob
        self.assertEqual(
            await self.refused(restores.status(self.web, bob, THURSDAY.id, rid)), "not_found"
        )
        self.assertEqual(
            await self.refused(restores.status(self.web, bob, THURSDAY.id, None)), "not_found"
        )
        self.assertEqual(
            await self.refused(restores.add_part(self.web, bob, THURSDAY.id, rid, 1, A_PART)),
            "not_found",
        )
        self.assertEqual(
            await self.refused(restores.go(self.web, bob, THURSDAY.id, rid, without=True)),
            "not_found",
        )
        self.assertEqual(
            await self.refused(restores.cancel(self.web, bob, THURSDAY.id, rid)), "not_found"
        )
        still = await restores.status(self.web, self.session, THURSDAY.id, rid)
        self.assertEqual(still["partsReceived"], 0)

    async def test_the_website_role_cannot_read_the_tables_themselves(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            for table in ("web_restores", "web_restore_parts"):
                cur = await conn.execute(
                    "SELECT has_table_privilege('dmbot_web', %s, 'SELECT') AS can", (table,)
                )
                row = await cur.fetchone()
                assert row is not None
                self.assertFalse(row["can"], table)

    async def test_the_status_never_carries_the_copy(self) -> None:
        made = await self.stage(a_copy("Secret Keep"))
        got = await restores.status(self.web, self.session, THURSDAY.id, made["id"])
        self.assertNotIn("main", got)
        self.assertEqual(got["name"], "Secret Keep")  # the name the person's own file gave

    async def test_the_latest_restore_is_found_without_its_id(self) -> None:
        made = await self.stage(a_copy())
        got = await restores.status(self.web, self.session, THURSDAY.id, None)
        self.assertEqual(got["id"], made["id"])

    # ---- progress ----

    async def test_the_writing_shows_how_many_files_are_done(self) -> None:
        made = await self.stage(a_copy(parts=4))
        rid = made["id"]
        for n in range(1, 5):
            await restores.add_part(self.web, self.session, THURSDAY.id, rid, n, A_PART)
        await restores.go(self.web, self.session, THURSDAY.id, rid, without=False)
        await self.db_sql(
            "UPDATE web_restores SET state = 'working', stage = 'restore_writing', done_parts = 3"
        )
        got = await restores.status(self.web, self.session, THURSDAY.id, rid)
        self.assertEqual(
            (got["stage"], got["done"], got["total"], got["unit"]),
            ("restore_writing", 3, 4, "parts"),
        )

    async def test_a_restore_the_bot_has_not_taken_goes_stalled(self) -> None:
        made = await self.stage(a_copy())
        later = NOW + progress.STALL_AFTER_S["restore_waiting"] + 60  # type: ignore[operator]
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT updated_at FROM web_restores")
            row = await cur.fetchone()
        assert row is not None
        got = await restores.status(
            self.web,
            self.session,
            THURSDAY.id,
            made["id"],
            now=int(row["updated_at"]) + later - NOW,
        )
        self.assertEqual((got["state"], got["reason"]), ("stalled", "stalled"))

    async def test_the_waiting_for_your_own_files_never_stalls(self) -> None:
        made = await self.stage(a_copy(parts=2))
        got = await restores.status(
            self.web, self.session, THURSDAY.id, made["id"], now=NOW + 10**7
        )
        self.assertEqual(got["state"], "running")

    async def test_a_finished_restore_names_the_campaign_and_the_lines_left_out(self) -> None:
        made = await self.stage(a_copy())
        await self.db_sql(
            "UPDATE web_restores SET state = 'done', stage = 'done', main = NULL,"
            " result_id = %s, left_out = 3",
            self.one.id,
        )
        got = await restores.status(self.web, self.session, THURSDAY.id, made["id"])
        self.assertEqual(
            (got["state"], got["campaign"], got["leftOut"]),
            ("done", f"{THURSDAY.id}.{self.one.id}", 3),
        )

    async def test_a_refusal_the_bot_wrote_comes_back_as_its_word(self) -> None:
        made = await self.stage(a_copy())
        for word in ("plan", "playing", "weird-thing"):
            await self.db_sql(
                "UPDATE web_restores SET state = 'failed', stage = 'failed', error = %s", word
            )
            got = await restores.status(self.web, self.session, THURSDAY.id, made["id"])
            self.assertEqual(
                (got["state"], got["reason"]),
                ("failed", word if word != "weird-thing" else "failed"),
            )

    # ---- cancel ----

    async def test_cancelling_takes_the_bytes_with_it_but_not_while_the_bot_works(self) -> None:
        made = await self.stage(a_copy(parts=1))
        rid = made["id"]
        await restores.add_part(self.web, self.session, THURSDAY.id, rid, 1, A_PART)
        await restores.cancel(self.web, self.session, THURSDAY.id, rid)
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT (SELECT count(*) FROM web_restores) AS r,"
                " (SELECT count(*) FROM web_restore_parts) AS p"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["r"], row["p"]), (0, 0))
        again = await self.stage(a_copy())
        await self.db_sql("UPDATE web_restores SET state = 'working'")
        self.assertEqual(
            await self.refused(restores.cancel(self.web, self.session, THURSDAY.id, again["id"])),
            "not_found",
        )


class TheRoute(Staging):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(self.client.aclose)
        self.base = f"/me/restores/{THURSDAY.id}"

    async def test_stage_send_and_watch_over_http(self) -> None:
        staged = await self.client.post(self.base, content=a_copy(parts=1), headers=HEADERS)
        self.assertEqual(staged.status_code, 202, staged.text)
        rid = staged.json()["id"]
        part = await self.client.put(f"{self.base}/{rid}/parts/1", content=A_PART, headers=HEADERS)
        self.assertEqual((part.status_code, part.json()), (200, {"received": 1}))
        sent = await self.client.post(
            f"{self.base}/{rid}/go", json={"without": False}, headers=HEADERS
        )
        self.assertEqual(sent.status_code, 202, sent.text)
        seen = await self.client.get(f"{self.base}/{rid}")
        self.assertEqual((seen.status_code, seen.json()["stage"]), (200, "restore_waiting"))
        latest = await self.client.get(self.base)
        self.assertEqual(latest.json()["id"], rid)
        gone = await self.client.delete(f"{self.base}/{rid}", headers=HEADERS)
        self.assertEqual(gone.status_code, 204)
        self.assertEqual((await self.client.get(f"{self.base}/{rid}")).status_code, 404)

    async def test_the_refusals_have_their_codes(self) -> None:
        bad = await self.client.post(self.base, content=b"nope", headers=HEADERS)
        self.assertEqual((bad.status_code, bad.json()["error"]), (422, "not_a_backup"))
        first = await self.client.post(self.base, content=a_copy(), headers=HEADERS)
        busy = await self.client.post(self.base, content=a_copy(), headers=HEADERS)
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.json(), {"error": "busy", "id": first.json()["id"]})
        early = await self.client.post(
            f"{self.base}/{first.json()['id']}/go", json={}, headers=HEADERS
        )
        self.assertEqual(early.status_code, 409)  # not receiving any more

    async def test_a_post_without_the_site_header_is_refused(self) -> None:
        refused = await self.client.post(self.base, content=a_copy())
        self.assertIn(refused.status_code, (400, 403))

    async def test_an_oversized_body_is_refused_while_it_is_read(self) -> None:
        big = await self.client.post(
            self.base, content=b"\0" * (restores.MAX_BODY_BYTES + 1), headers=HEADERS
        )
        self.assertEqual((big.status_code, big.json()["error"]), (413, "too_big"))
