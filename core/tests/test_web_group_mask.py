"""The website applies the group mask as Discord does (#4807, party knowledge slice 5a): a faction's
who-knows row on a statement the DM hid, or has not confirmed, reaches none of its player
characters on the player's own page or the DM's Players View, while a secret the DM gave the
faction still does. Postgres. Invented names."""

from __future__ import annotations

from dmbot.memory import gate
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import relationships
from tests.test_web_relationships import Web


class AFactionsMembersOnTheWeb(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        assert self.hero is not None
        self.hand = await self.add("The Gray Hand", "faction")
        for member in (self.hero, self.rook):
            await self.link("member_of", member, self.hand, f"{member}-hand")
        self.vault = await self.add("The vault", "place")
        # (the test helper tells every player character a new name's main name; here only the
        # faction knows the vault, so what reaches its members is the group's row alone)
        await self.sql(
            "DELETE FROM memory_knowers WHERE alias_id IN"
            " (SELECT id FROM memory_aliases WHERE entity_id = %s)",
            self.vault,
        )
        await self.memory.set_statement_knowers(
            self.g, self.one.id, gate.ENTRY, self.vault, [self.hand],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip

    async def hide(self, entry: str, hidden: bool = True) -> None:
        await self.sql(
            "UPDATE memory_entities SET hidden_from_players = %s WHERE id = %s", hidden, entry
        )

    async def can_open(self, who: object, **kw: object) -> bool:
        try:
            await self.graph(who, center=self.vault, hops=1, **kw)
        except Exception:
            return False
        return True

    async def test_a_hidden_statement_the_faction_knows_does_not_reach_a_player(self) -> None:
        self.assertTrue(await self.can_open(self.player))  # (shown: the Hand's, through Bob's hero)
        await self.hide(self.vault)
        self.assertFalse(await self.can_open(self.player))
        await self.hide(self.vault, False)
        self.assertTrue(await self.can_open(self.player))  # (shown again)

    async def test_the_dms_players_view_leaves_it_out_too(self) -> None:
        self.assertTrue(await self.can_open(self.session, view="players"))
        await self.hide(self.vault)
        self.assertFalse(await self.can_open(self.session, view="players"))
        self.assertTrue(await self.can_open(self.session))  # (the DM's own page keeps it)

    async def test_a_player_character_who_left_is_masked_too(self) -> None:
        await self.hide(self.vault)
        assert self.hero is not None
        await self.memory.leave_party(self.g, self.one.id, self.hero, "left")
        self.assertFalse(await self.can_open(self.player))

    async def test_a_secret_connection_given_to_the_faction_still_reaches_its_members(self) -> None:
        mine = await self.add("The old mine", "place")
        tie = await self.link("located_in", self.vault, mine, "vault-mine", secret=True)
        await self.memory.set_statement_knowers(
            self.g, self.one.id, gate.ENTRY, mine, [self.hand], source=DM_SOURCE,
            dm_chose_secret=True,
        )  # fmt: skip
        await self.memory.set_statement_knowers(
            self.g, self.one.id, gate.CONNECTION, tie, [self.hand], source=DM_SOURCE,
            dm_chose_secret=True,
        )  # fmt: skip
        page = await self.graph(self.player, center=self.vault, hops=1)
        seen = [x["id"] for e in page["edges"] for x in e["links"]]
        self.assertIn(tie, seen)

    async def test_a_connection_to_a_hidden_entry_is_left_out(self) -> None:
        tie = await self.link("located_in", self.vault, self.keep, "vault-keep")
        await self.memory.set_statement_knowers(
            self.g, self.one.id, gate.CONNECTION, tie, [self.hand], source=DM_SOURCE,
            dm_chose_secret=True,
        )  # fmt: skip
        page = await self.graph(self.player, center=self.keep, hops=1)
        self.assertIn(tie, [x["id"] for e in page["edges"] for x in e["links"]])
        await self.hide(self.vault)
        page = await self.graph(self.player, center=self.keep, hops=1)
        self.assertNotIn(tie, [x["id"] for e in page["edges"] for x in e["links"]])

    async def test_the_door_and_the_bot_gate_agree_on_what_is_dark(self) -> None:
        # (the website's rule must stay in step with `gate._not_shown`: both on the same rows)
        tie = await self.link("located_in", self.vault, self.keep, "vault-keep")
        await self.memory.set_statement_knowers(
            self.g, self.one.id, gate.CONNECTION, tie, [self.hand], source=DM_SOURCE,
            dm_chose_secret=True,
        )  # fmt: skip
        await self.alias(self.vault, "The Sealed Door")  # (an other name of a hidden entry too)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT id FROM memory_aliases WHERE entity_id = %s AND text = 'The Sealed Door'",
                (self.vault,),
            )
            row = await cur.fetchone()
        assert row is not None
        name = str(row["id"])
        await self.memory.set_statement_knowers(
            self.g, self.one.id, gate.ALIAS, name, [self.hand], source=DM_SOURCE,
            dm_chose_secret=True,
        )  # fmt: skip
        await self.hide(self.keep)  # (a connection to a hidden end goes dark too)
        await self.hide(self.vault)
        _src, known, _pages = await relationships.source_of(self.web, self.session, self.cref)
        async with self.memory._read(self.g, self.one.id) as scope:
            bot = await gate.load(scope)
        self.assertEqual(
            {(str(k), str(i)) for k, i in known["dark"]}, set(bot.dark), "the door drifted"
        )
        self.assertEqual(set(known["allPlayers"]), set(bot.players))
        self.assertIn((gate.ENTRY, self.vault), bot.dark)
        self.assertIn((gate.CONNECTION, tie), bot.dark)
        self.assertIn((gate.ALIAS, name), bot.dark)

    async def test_a_person_the_story_never_named_is_masked_too(self) -> None:
        # (0264: every guard on `hidden_from_players` keeps the unnamed out as well)
        await self.memory.set_statement_knowers(
            self.g, self.one.id, gate.ENTRY, self.vault, [self.hand], source=DM_SOURCE,
            dm_chose_secret=True,
        )  # fmt: skip
        self.assertTrue(await self.can_open(self.player))
        await self.sql(
            "UPDATE memory_entities SET unnamed = true, unnamed_what = 'a stranger' WHERE id = %s",
            self.vault,
        )
        # (the door counts a person the story never named as hidden, as every other guard on
        # `hidden_from_players` does; `gate._not_shown` does not yet, so the drift test above
        # stays on named entries)
        _src, known, _pages = await relationships.source_of(self.web, self.session, self.cref)
        self.assertIn((gate.ENTRY, self.vault), {(str(k), str(i)) for k, i in known["dark"]})
