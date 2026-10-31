"""The DM's question of who is in each split group (#3131): the buttons, the pickers, and what a
pick saves. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.dm_screen import clock as ui
from dmbot.memory.store import MemoryStore
from dmbot.timebot import split
from dmbot.timebot.store import NOT_A_DM
from tests import test_split_party as sp
from tests.test_split_party import DM, GUILD_A, PLAYER


class Who(sp.Party):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.vell = await self.character("Vell", 101)
        self.tuck = await self.character("Tuck", 102)
        await self.memory.add_entity(
            GUILD_A, self.a.id, name="Guard", type="npc", status="confirmed", source="dm"
        )  # (not a player character: never offered)

    async def character(self, name: str, player: int) -> str:
        made = await self.memory.add_entity(
            GUILD_A,
            self.a.id,
            name=name,
            type="character",
            role="player_character",
            played_by=player,
            status="confirmed",
            source="dm",
        )
        return str(made.value.id)

    async def message(self) -> tuple[str, Any]:
        stored = await self.stored()
        return await ui.placement_message(self.fake, self.a.id, GUILD_A, stored.groups)

    def selects(self, view: Any) -> list[Any]:
        return [c for c in view.children if hasattr(c, "options")]


class TheQuestion(Who):
    async def test_one_picker_per_group_with_the_campaigns_player_characters_only(self) -> None:
        await self.split_up("Tower", "Camp")
        content, view = await self.message()
        self.assertIn("Pick who is in each group.", content)
        self.assertIn("Anyone you skip gets no game time on their lines until you do.", content)
        pickers = self.selects(view)
        self.assertEqual([p.placeholder for p in pickers], ["Who is in Tower?", "Who is in Camp?"])
        for picker in pickers:
            self.assertEqual(sorted(o.label for o in picker.options), ["Tuck", "Vell"])
            self.assertEqual(picker.min_values, 0)
        self.assertEqual(
            [c.label for c in view.children if not hasattr(c, "options")], ["Skip for now"]
        )

    async def test_a_campaign_with_no_player_characters_says_so_and_offers_nothing(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Gamma", DM)
        await self.clocks.change(GUILD_A, other.id, DM, lambda _c: sp.Clock(1000, 0))
        await self.clocks.split(GUILD_A, other.id, DM, ["Tower", "Camp"])
        stored = await self.clocks.get(GUILD_A, other.id)
        assert stored is not None
        content, view = await ui.placement_message(self.fake, other.id, GUILD_A, stored.groups)
        self.assertEqual((content, view), (ui.WHO_NONE, None))

    async def test_the_panel_has_who_is_where_only_while_split(self) -> None:
        def labels(is_split: bool) -> list[str]:
            children: list[Any] = list(ui.panel_view(self.a.id, is_split).children)
            return [str(getattr(i, "item", i).label) for i in children]

        self.assertNotIn("Who is where", labels(False))
        self.assertIn("Who is where", labels(True))

    async def test_the_button_answers_only_a_dm_and_only_while_split(self) -> None:
        button = ui.ClockButton(self.a.id, "who")
        together = self.interaction()
        await button._who(together, self.fake, GUILD_A)
        self.assertEqual(together.followup.send.await_args.args[0], split.NOT_SPLIT)
        await self.split_up()
        player = self.interaction(PLAYER)
        await button._who(player, self.fake, GUILD_A)
        self.assertEqual(player.followup.send.await_args.args[0], NOT_A_DM)
        dm = self.interaction()
        await button._who(dm, self.fake, GUILD_A)
        sent = dm.response.send_message.await_args
        self.assertIn("Pick who is in each group.", sent.args[0])
        self.assertTrue(sent.kwargs["ephemeral"])


class Picking(Who):
    async def pick(self, picker: Any, chosen: list[str], user: int = DM) -> Any:
        interaction = self.interaction(user)
        picker._values = chosen  # (what Discord sends back)
        await picker.callback(interaction)
        return interaction

    async def test_a_pick_places_them_and_redraws_with_the_group_filled_in(self) -> None:
        await self.split_up("Tower", "Camp")
        _, view = await self.message()
        tower, camp = self.selects(view)
        done = await self.pick(tower, [self.vell])
        self.assertEqual(await self.clocks.members(GUILD_A, self.a.id), {self.vell: 1})
        edited = done.response.edit_message.await_args.kwargs
        self.assertIn("**Tower**: Vell", edited["content"])
        self.assertIn("**Camp**: nobody yet", edited["content"])
        await self.pick(camp, [self.tuck])
        self.assertEqual(
            await self.clocks.members(GUILD_A, self.a.id), {self.vell: 1, self.tuck: 2}
        )

    async def test_taking_someone_off_the_pick_releases_them(self) -> None:
        await self.split_up()
        _, view = await self.message()
        tower = self.selects(view)[0]
        await self.pick(tower, [self.vell, self.tuck])
        await self.pick(tower, [self.tuck])
        self.assertEqual(await self.clocks.members(GUILD_A, self.a.id), {self.tuck: 1})

    async def test_a_player_cannot_place_anyone_and_is_told_so(self) -> None:
        await self.split_up()
        _, view = await self.message()
        done = await self.pick(self.selects(view)[0], [self.vell], user=PLAYER)
        self.assertEqual(await self.clocks.members(GUILD_A, self.a.id), {})
        self.assertEqual(done.response.send_message.await_args.args[0], NOT_A_DM)

    async def test_skip_closes_the_question_and_places_nobody(self) -> None:
        await self.split_up()
        _, view = await self.message()
        skip = next(c for c in view.children if not hasattr(c, "options"))
        done = self.interaction()
        await skip.callback(done)
        self.assertEqual(done.response.edit_message.await_args.kwargs["content"], ui.WHO_SKIPPED)
        self.assertEqual(await self.clocks.members(GUILD_A, self.a.id), {})

    async def test_the_picker_holds_at_most_what_a_select_can_show(self) -> None:
        await self.split_up()
        for n in range(30):
            await self.character(f"Extra {n:02d}", 200 + n)
        _, view = await self.message()
        self.assertTrue(all(len(p.options) <= ui.SELECT_MAX for p in self.selects(view)))
