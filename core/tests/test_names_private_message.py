"""The check of new names, opened from the DM's private message (#2977): a private message has no
server, so its cards' buttons used to answer "Use this in a server". Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import PROPOSED
from dmbot.ui import names as ui
from tests.test_memory_names import GUILD, NamesTest


class FromAPrivateMessage(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        ui._PRIVATE_SERVERS.clear()

    async def asyncTearDown(self) -> None:
        ui._PRIVATE_SERVERS.clear()
        await super().asyncTearDown()

    def private(self, user: int = 0) -> Any:  # (a stand-in interaction, as the other tests use)
        it = self.it(user) if user else self.it()
        it.guild = None
        it.guild_id = None
        return it

    async def test_not_a_name_works_from_a_private_message_the_dm_opened(self) -> None:
        await self.memory.add_entity(
            GUILD, self.campaign.id, type="concept", name="Ambush", source="scan",
            description="Heard 3 times",
        )  # fmt: skip
        ui._PRIVATE_SERVERS[self.campaign.id] = GUILD  # (what the DM's button press does)
        it = self.private()
        await ui.start_review(it, self.campaign.id, bulk=False)
        view = it.response.sent[0][1]["view"]
        press = self.private()
        await view._no(press)
        sent = [t for t, _ in press.response.sent] + [t for t, _ in press.response.edited]
        self.assertFalse(any("Use this in a server" in t for t in sent), sent)
        self.assertFalse(await self.names((PROPOSED,)))  # (it was answered)

    async def test_with_no_server_known_it_still_says_so(self) -> None:
        it = self.private()
        self.assertIsNone(await ui._campaign_for(it, self.campaign.id))
        self.assertIn("Use this in a server", it.response.sent[0][0])

    async def test_a_person_who_may_not_run_the_campaign_gets_nothing(self) -> None:
        ui._PRIVATE_SERVERS[self.campaign.id] = GUILD
        it = self.private(user=4242)
        self.assertIsNone(await ui._campaign_for(it, self.campaign.id))
