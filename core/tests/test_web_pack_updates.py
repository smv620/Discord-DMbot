"""The website's pack updates (#4841 packs slice, migration 0566): only a DM reads them, a followed
pack with a newer version gives core's own lines (worked out by `rules.pack_diff`, as Discord's
note), a pack that stopped updating or is on the latest gives none, an update DMbot took shows
with the copy before it, a damaged copy is left out, and a share code never leaves. Postgres.
Invented names."""

from __future__ import annotations

import json
from typing import Any

from dmbot.rules import pack_diff
from dmbot.web import pack_updates
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_rule_sharing import SET_A, SharingBase


def pack(*entries: tuple[str, str]) -> dict[str, Any]:
    return {
        "entries": [
            {"kind": "item", "name": n, "summary": None, "text": t, "known": False}
            for n, t in entries
        ]
    }


OLD = pack(("Lamp", "Glows."), ("Rope", "Long."))
NEW = pack(("Lamp", "Glows."), ("Rope", "Very long."), ("Flute", "Plays."))


class PackUpdates(SharingBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.publish(self.two.id, SET_A, "Frost Pack")  # (versions 1 and 2)
        await self.sql(
            "UPDATE rule_set_versions SET content = %s::jsonb WHERE set_id = %s AND version = 2",
            json.dumps(NEW), SET_A,
        )  # fmt: skip
        await self.follow_pack(OLD)

    async def follow_pack(self, content: dict[str, Any], **extra: Any) -> None:
        cols = {"version": 1, "updating": True, "previous_version": None,
                "previous_content": None, **extra}  # fmt: skip
        await self.sql("DELETE FROM rule_set_follows")
        await self.sql(
            "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
            " publisher, version, content, updating, previous_version, previous_content,"
            " followed_by, followed_at) VALUES (%s, %s, %s, 'homebrew', 'Frost Pack', 'Two',"
            " %s, %s::jsonb, %s, %s, %s::jsonb, %s, 5)",
            THURSDAY.id, self.one.id, SET_A, cols["version"], json.dumps(content),
            cols["updating"], cols["previous_version"],
            None if cols["previous_content"] is None else json.dumps(cols["previous_content"]),
            ALICE.id,
        )  # fmt: skip

    async def fetch(self, session: Any = None) -> Any:
        return await pack_updates.read(self.web, session or self.session, self.cref())

    async def test_a_newer_version_gives_core_s_own_lines(self) -> None:
        got = await self.fetch()
        changes = pack_diff.diff(OLD, NEW)
        self.assertEqual(
            got["waiting"],
            [{
                "setId": SET_A, "name": "Frost Pack", "publisher": "Two", "from": 1, "to": 2,
                "words": pack_diff.count_words(changes),
                "lines": [pack_diff.describe(c) for c in changes], "more": 0,
            }],
        )  # fmt: skip
        self.assertEqual((got["waiting"][0]["words"], got["taken"]), ("2 entries", []))
        self.assertNotIn("code", json.dumps(got).lower().replace("code of", ""))

    async def test_nothing_waits_when_on_the_latest_or_no_longer_updating(self) -> None:
        await self.follow_pack(NEW, version=2)
        self.assertEqual((await self.fetch())["waiting"], [])
        await self.follow_pack(OLD, updating=False)
        self.assertEqual((await self.fetch())["waiting"], [])

    async def test_a_pack_that_stopped_sharing_gives_nothing(self) -> None:
        await self.sql("UPDATE rule_sets SET status = 'unshared' WHERE id = %s", SET_A)
        self.assertEqual((await self.fetch())["waiting"], [])

    async def test_a_taken_update_shows_with_the_copy_before_it(self) -> None:
        await self.follow_pack(NEW, version=2, previous_version=1, previous_content=OLD)
        got = await self.fetch()
        changes = pack_diff.diff(OLD, NEW)
        self.assertEqual(got["waiting"], [])
        self.assertEqual(
            [(t["from"], t["to"], t["lines"]) for t in got["taken"]],
            [(1, 2, [pack_diff.describe(c) for c in changes])],
        )

    async def test_a_damaged_copy_is_left_out_and_house_rules_are_not_packs(self) -> None:
        await self.follow_pack({"entries": "nope"})
        self.assertEqual(await self.fetch(), {"waiting": [], "taken": []})
        await self.follow_pack(OLD)
        await self.sql("UPDATE rule_set_follows SET kind = 'house_rules'")
        self.assertEqual(await self.fetch(), {"waiting": [], "taken": []})

    async def test_a_player_is_refused(self) -> None:
        with self.assertRaises(RecapError) as err:
            await self.fetch(await self.signed(BOB))
        self.assertEqual(err.exception.code, "not_allowed")
