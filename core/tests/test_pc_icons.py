"""The Story Graph's player character icons (#3621): what each character draws (the player's
Discord avatar, else the D&D Beyond portrait, else nothing), the choice per character, who may
change it, and the backup. Invented names and hashes throughout."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.consent import ConsentStore
from dmbot.members import MemberDirectory
from dmbot.memory.pc_icons_backup import DAMAGED, PcIconsSection
from dmbot.web import pc_icons
from dmbot.web.discord import DiscordUser
from dmbot.web.recaps import RecapError, _scoped
from tests.test_web_all_names import MEMBER
from tests.test_web_relationships import BOB_USER, Web

HASH = "cd" * 16
PORTRAIT = "https://www.dndbeyond.com/avatars/12/345/637000000000000000.jpeg"
CAROL = DiscordUser(id=2003, name="Carol", email="carol@example.com")


class Icons(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.directory = MemberDirectory(self.db)
        await ConsentStore(self.db).grant(self.g, BOB_USER.id)
        await self.directory.remember(self.g, BOB_USER.id, "Bob", "bob_b")
        self.cref_ = self.cref

    def avatar_url(self) -> str:
        return f"https://cdn.discordapp.com/avatars/{BOB_USER.id}/{HASH}.png?size=128"

    async def give_avatar(self, key: str | None = HASH) -> None:
        await self.directory.set_avatar(self.g, BOB_USER.id, key)

    async def give_portrait(self, link: str | None = PORTRAIT) -> None:
        await self.sql("DELETE FROM character_sheets WHERE entity_id = %s", self.hero)
        await self.sql(
            "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, sheet, source,"
            " fetched_at, player_id) VALUES (%s, %s, %s, %s::jsonb, 'typed', 1, %s)",
            self.g, self.one.id, self.hero, '{"portrait": %s}' % (f'"{link}"' if link else "null"),
            BOB_USER.id,
        )  # fmt: skip

    async def rows(self, who: Any = None) -> dict[str, dict[str, Any]]:
        who = who or self.session
        async with self.web.user(who.user_id, session=who.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_pc_icons(%s, %s) AS r", (self.g, self.one.id)
            )
            found = await cur.fetchone()
        assert found is not None and found["r"] is not None
        return {str(r["id"]): r for r in found["r"]}

    async def icon(self, who: Any = None) -> Any:
        return (await self.rows(who))[str(self.hero)]["icon"]

    async def choose(self, choice: str, who: Any = None, entity: str | None = None) -> Any:
        return await pc_icons.set_icon(
            self.web, who or self.session, self.cref, entity or str(self.hero), {"choice": choice}
        )

    async def refused_icon(self, choice: Any, who: Any = None, entity: str | None = None) -> str:
        try:
            await pc_icons.set_icon(
                self.web,
                who or self.session,
                self.cref,
                entity or str(self.hero),
                {"choice": choice},
            )
        except RecapError as err:
            return err.code
        raise AssertionError("was not refused")


class TheDefault(Icons):
    async def test_the_players_discord_avatar_comes_first(self) -> None:
        await self.give_avatar()
        await self.give_portrait()
        self.assertEqual(await self.icon(), {"kind": "discord", "url": self.avatar_url()})

    async def test_then_the_dndbeyond_portrait(self) -> None:
        await self.give_portrait()
        self.assertEqual(await self.icon(), {"kind": "dndbeyond", "url": PORTRAIT})

    async def test_then_nothing_and_the_page_draws_its_own(self) -> None:
        self.assertIsNone(await self.icon())

    async def test_a_player_who_never_agreed_has_no_avatar(self) -> None:
        await self.sql("DELETE FROM member_directory WHERE user_id = %s", BOB_USER.id)
        await self.give_portrait()
        self.assertEqual((await self.icon())["kind"], "dndbeyond")  # (the portrait is the sheet's)

    async def test_relinking_the_character_changes_the_avatar_at_once(self) -> None:
        await self.give_avatar()
        await self.sql("UPDATE memory_entities SET played_by = NULL WHERE id = %s", self.hero)
        self.assertIsNone(await self.icon())

    async def test_a_link_off_the_picture_hosts_is_never_given(self) -> None:
        await self.give_portrait("https://evil.example/avatars/1/2/3.png")
        self.assertIsNone(await self.icon())


class TheChoice(Icons):
    async def test_each_choice_and_remove(self) -> None:
        await self.give_avatar()
        await self.give_portrait()
        self.assertEqual(
            await self.choose("dndbeyond"), {"previous": "default", "now": "dndbeyond"}
        )
        self.assertEqual(await self.icon(), {"kind": "dndbeyond", "url": PORTRAIT})
        await self.choose("discord")
        self.assertEqual((await self.icon())["kind"], "discord")
        await self.choose("hidden")
        self.assertEqual(await self.icon(), {"kind": "hidden", "url": None})
        self.assertEqual(await self.choose("default"), {"previous": "hidden", "now": "default"})
        self.assertEqual((await self.icon())["kind"], "discord")  # the default order again

    async def test_undo_is_writing_the_previous_choice_back(self) -> None:
        await self.give_avatar()
        await self.choose("hidden")
        done = await self.choose("discord")
        await self.choose(done["previous"])
        self.assertEqual((await self.rows())[str(self.hero)]["choice"], "hidden")

    async def test_a_choice_whose_picture_is_gone_reads_as_none(self) -> None:
        await self.give_avatar()
        await self.choose("discord")
        await self.give_avatar(None)
        self.assertIsNone(await self.icon())

    async def test_a_picture_that_does_not_exist_cannot_be_chosen(self) -> None:
        self.assertEqual(await self.refused_icon("discord"), "unavailable")
        self.assertEqual(await self.refused_icon("dndbeyond"), "unavailable")

    async def test_only_the_four_choices_are_taken(self) -> None:
        for bad in ("custom", "", None, 5, "https://evil.example/x.png"):
            self.assertEqual(await self.refused_icon(bad), "bad_choice", bad)

    async def test_a_node_that_is_not_a_confirmed_player_character_is_not_found(self) -> None:
        self.assertEqual(await self.refused_icon("hidden", entity=str(self.mira)), "not_found")
        self.assertEqual(await self.refused_icon("hidden", entity="f" * 32), "not_found")


class WhoMaySet(Icons):
    async def test_the_dm_and_the_characters_own_player_may(self) -> None:
        await self.choose("hidden")  # Alice, the DM
        await self.choose("default", self.player)  # Bob plays it

    async def test_another_player_and_another_campaigns_dm_may_not(self) -> None:
        other = await self.signed(CAROL, MEMBER)
        self.assertEqual(await self.refused_icon("hidden", other), "not_allowed")
        rows = await self.rows(other)
        self.assertFalse(rows[str(self.hero)]["canSet"])
        self.assertNotIn("has", rows[str(self.hero)])  # (they are not told which pictures exist)

    async def test_the_controls_flag_and_the_pictures_that_exist_go_to_those_who_may(self) -> None:
        await self.give_avatar()
        for who in (self.session, self.player):
            row = (await self.rows(who))[str(self.hero)]
            self.assertTrue(row["canSet"])
            self.assertEqual(row["has"], {"discord": True, "dndbeyond": False})

    async def test_someone_outside_the_server_is_refused(self) -> None:
        stranger = await self.signed(CAROL)  # signed in, in no server
        with self.assertRaises(RecapError):
            await pc_icons.set_icon(
                self.web, stranger, self.cref, str(self.hero), {"choice": "hidden"}
            )

    async def test_a_dm_of_another_campaign_may_not(self) -> None:
        other = await self.campaigns.create(self.g, "Elsewhere", BOB_USER.id)  # Bob DMs it
        # (Bob plays Hero in "One"; in his own campaign "Elsewhere" the character is not found)
        async with self.web.user(self.player.user_id, session=self.player.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_pc_icon_set(%s, %s, %s, 'hidden') AS r",
                (self.g, other.id, str(self.hero)),
            )
            found = await cur.fetchone()
        assert found is not None
        self.assertEqual(found["r"]["status"], "not_found")


class OnThePage(Icons):
    async def test_the_graph_page_carries_the_icon_only_on_nodes_it_has(self) -> None:
        await self.give_avatar()
        page = {"nodes": [{"id": str(self.hero)}, {"id": str(self.mira)}]}
        await pc_icons.with_icons(self.web, self.session, self.cref, page)
        hero, mira = page["nodes"]
        self.assertEqual(hero["icon"], {"kind": "discord", "url": self.avatar_url()})
        self.assertEqual((hero["iconChoice"], hero["canSetIcon"]), ("default", True))
        self.assertNotIn("icon", mira)  # (an NPC has no icon)

    async def test_a_hidden_choice_is_hidden_for_every_viewer(self) -> None:
        await self.give_avatar()
        await self.choose("hidden")
        for who in (self.session, self.player):
            self.assertEqual(await self.icon(who), {"kind": "hidden", "url": None})


class InBackups(Icons):
    async def test_the_choice_goes_in_and_comes_back_the_same(self) -> None:
        await self.choose("hidden")
        async with self.db.guild(self.g) as conn:
            dumped = await PcIconsSection().dump(conn, self.g, self.one.id)
        self.assertEqual(
            [(r["entity_id"], r["choice"]) for r in dumped], [(str(self.hero), "hidden")]
        )
        async with self.db.guild(self.g) as conn:
            await PcIconsSection().clear(conn, self.g, self.one.id)
            await PcIconsSection().load(conn, self.g, self.one.id, dumped)
            again = await PcIconsSection().dump(conn, self.g, self.one.id)
        self.assertEqual(again, dumped)

    async def test_a_damaged_file_is_refused_whole(self) -> None:
        good = {
            "entity_id": "a" * 32,
            "choice": "hidden",
            "custom_ref": None,
            "set_by": 5,
            "updated_at": 9,
        }
        section = PcIconsSection()
        for bad in (
            {**good, "choice": "https://evil.example"},
            {**good, "custom_ref": "b" * 32},  # only `custom` has a reference
            {**good, "choice": "custom"},  # ... and it needs one
            {**good, "entity_id": "../x"},
            {**good, "set_by": True},
            {**good, "extra": 1},
        ):
            with self.assertRaises(CampaignError, msg=bad) as caught:
                section.check([bad])
            self.assertEqual(str(caught.exception), DAMAGED)
        with self.assertRaises(CampaignError):
            section.check([good, good])  # the same character twice
        section.check([good])

    async def test_a_choice_for_an_entry_the_campaign_lacks_is_dropped(self) -> None:
        row = {
            "entity_id": "e" * 32,
            "choice": "hidden",
            "custom_ref": None,
            "set_by": 5,
            "updated_at": 9,
        }
        async with self.db.guild(self.g) as conn:
            await PcIconsSection().load(conn, self.g, self.one.id, [row])
            self.assertEqual(await PcIconsSection().dump(conn, self.g, self.one.id), [])
