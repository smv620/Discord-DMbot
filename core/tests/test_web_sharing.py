"""The Shared rules page's data (#2460 part 2), run as the website's own database role: only the
DMs of a campaign see what it publishes and follows, a player and a stranger see nothing, a share
code is reported as on or off and never shown, another campaign's sets stay out, and the door keeps
its pinned search path and no public execute."""

from __future__ import annotations

import json
from typing import Any

from dmbot.web import sessions, sharing
from tests.test_recap_web import BOB, NOW, Base, callers
from tests.test_web_api import ALICE, QUILLON, THURSDAY

CAROL = type(BOB)(id=3003, name="Carol", email="carol@example.com")  # in the server, no DM at all
SET_A = "a" * 32
SET_B = "b" * 32
SECRET_CODE = "ABCDEFGHJK"


class SharingBase(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.g = THURSDAY.id

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(statement, args)

    async def publish(
        self, campaign: str, set_id: str, kind: str, name: str, *, status: str = "shared"
    ) -> None:
        await self.sql(
            "INSERT INTO rule_sets (guild_id, campaign_id, id, kind, name, status, created_by,"
            " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 5)",
            self.g, campaign, set_id, kind, name, status, ALICE.id,
        )  # fmt: skip
        for version in (1, 2):
            await self.sql(
                "INSERT INTO rule_set_versions (guild_id, campaign_id, set_id, version, content,"
                " created_by, created_at) VALUES (%s, %s, %s, %s, %s::jsonb, %s, 5)",
                self.g, campaign, set_id, version, json.dumps({"rules": []}), ALICE.id,
            )  # fmt: skip

    async def code(self, campaign: str, set_id: str, *, active: bool = True) -> None:
        await self.sql(
            "INSERT INTO rule_set_codes (guild_id, campaign_id, code, set_id, active, created_at)"
            " VALUES (%s, %s, %s, %s, %s, 5)",
            self.g, campaign, SECRET_CODE, set_id, active,
        )  # fmt: skip

    async def follow(self, campaign: str, set_id: str, kind: str, name: str, **kw: Any) -> None:
        await self.sql(
            "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
            " publisher, version, content, updating, followed_by, followed_at)"
            " VALUES (%s, %s, %s, %s, %s, 'Their Table', 3, '{}'::jsonb, %s, %s, 5)",
            self.g, campaign, set_id, kind, name, kw.get("updating", True), ALICE.id,
        )  # fmt: skip

    async def signed(self, user: Any, guilds: Any = None) -> Any:
        token = await sessions.sign_in(
            self.web, user, guilds if guilds is not None else [THURSDAY], now=NOW, days=30
        )
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found


class TheSharing(SharingBase):
    async def test_a_dm_sees_what_their_campaign_publishes_and_follows(self) -> None:
        await self.publish(self.one.id, SET_A, "house_rules", "Our Rules")
        await self.code(self.one.id, SET_A)
        await self.follow(self.one.id, SET_B, "homebrew", "Frost Pack", updating=False)
        got = await sharing.list_sharing(self.web, self.session)
        self.assertEqual([c["ref"] for c in got], [f"{self.g}.{self.one.id}"])
        card = got[0]
        self.assertEqual(card["name"], "One")
        self.assertEqual(
            card["publishes"],
            [{"kind": "house-rules", "name": "Our Rules", "version": 2, "shared": True,
              "hasCode": True}],
        )  # fmt: skip
        self.assertEqual(
            card["follows"],
            [{"kind": "homebrew", "name": "Frost Pack", "publisher": "Their Table",
              "version": 3, "updating": False}],
        )  # fmt: skip

    async def test_a_share_code_is_never_in_the_answer(self) -> None:
        await self.publish(self.one.id, SET_A, "house_rules", "Our Rules")
        await self.code(self.one.id, SET_A)
        got = await sharing.list_sharing(self.web, self.session)
        self.assertNotIn(SECRET_CODE, json.dumps(got))

    async def test_an_off_code_and_a_stopped_set_read_as_such(self) -> None:
        await self.publish(self.one.id, SET_A, "homebrew", "Old Pack", status="unshared")
        await self.code(self.one.id, SET_A, active=False)
        pack = (await sharing.list_sharing(self.web, self.session))[0]["publishes"][0]
        self.assertFalse(pack["shared"])
        self.assertFalse(pack["hasCode"])

    async def test_a_campaign_with_no_sharing_is_left_out(self) -> None:
        self.assertEqual(await sharing.list_sharing(self.web, self.session), [])

    async def test_another_dms_campaign_is_not_in_this_persons_answer(self) -> None:
        await self.publish(self.two.id, SET_A, "house_rules", "Bob's Rules")
        self.assertEqual(await sharing.list_sharing(self.web, self.session), [])
        bobs = await sharing.list_sharing(self.web, await self.signed(BOB))
        self.assertEqual([c["name"] for c in bobs], ["Two"])

    async def test_a_player_and_a_stranger_see_nothing(self) -> None:
        await self.publish(self.one.id, SET_A, "house_rules", "Our Rules")
        await self.follow(self.one.id, SET_B, "homebrew", "Frost Pack")
        mira = await self.sql_player()
        self.assertTrue(mira)
        self.assertEqual(await sharing.list_sharing(self.web, await self.signed(CAROL)), [])
        elsewhere = await self.signed(ALICE, [QUILLON])
        self.assertEqual(await sharing.list_sharing(self.web, elsewhere), [])

    async def sql_player(self) -> bool:
        # Carol plays in the campaign (a claimed character) but does not run it.
        from dmbot.memory.models import DM as DM_SOURCE
        from dmbot.memory.store import MemoryStore

        made = await MemoryStore(self.db).add_entity(
            self.g, self.one.id, type="character", name="Mira", source=DM_SOURCE, status="confirmed"
        )
        await self.sql(
            "UPDATE memory_entities SET role = 'player_character', played_by = %s WHERE id = %s",
            CAROL.id, made.value.id,
        )  # fmt: skip
        return True

    async def test_a_server_outside_the_session_list_gets_nothing_from_the_door(self) -> None:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g + 1),))
            cur = await conn.execute("SELECT dmbot_web_shared_rules(%s) AS f", (self.g + 1,))
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["f"])


class TheDoor(Base):
    async def test_the_door_has_one_caller(self) -> None:
        self.assertEqual(callers("dmbot_web_shared_rules("), {"schema.py", "web/sharing.py"})

    async def test_the_door_keeps_its_pinned_search_path_and_no_public_execute(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT proconfig::text AS cfg, prosecdef,"
                " has_function_privilege('public', oid, 'EXECUTE') AS public_may"
                " FROM pg_proc WHERE proname = 'dmbot_web_shared_rules'"
            )
            rows = await cur.fetchall()
        self.assertGreaterEqual(len(rows), 1)
        for row in rows:
            self.assertIn("search_path", row["cfg"] or "")
            self.assertTrue(row["prosecdef"])
            self.assertFalse(row["public_may"])
