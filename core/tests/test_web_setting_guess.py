"""The website's All names door and DMbot's guess at the setting (#4173 slice 2b; the reviewer on
#4173): someone who isn't a DM of the campaign gets `setting` false for a guess, and the DM gets
`settingFrom` to mark it. Invented names."""

from __future__ import annotations

from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_name_actions import Acting


class TheDoor(Acting):
    async def test_a_guess_is_the_dms_alone_and_marked(self) -> None:
        await self.memory.set_place_level(self.g, self.one.id, self.keep, "world", source="dm")
        keep = (await self.memory.entity(self.g, self.one.id, self.keep)).name  # type: ignore[union-attr]
        made = await self.memory.guess_setting(self.g, self.one.id, keep, source="background")
        assert made.value is not None and made.value.id == self.keep
        dm_row = next(n for n in (await self.page())["names"] if n["id"] == self.keep)
        self.assertEqual((dm_row["setting"], dm_row["settingFrom"]), (True, "read"))
        bob = await self.signed(BOB, THURSDAY)
        seen = await self.page(bob)
        other = next(n for n in seen["names"] if n["id"] == self.keep)
        self.assertEqual((other["setting"], other["settingFrom"]), (False, None))
        await self.memory.keep_setting(self.g, self.one.id, self.keep, source="dm")
        other = next(n for n in (await self.page(bob))["names"] if n["id"] == self.keep)
        self.assertEqual(other["setting"], True)  # (kept: the plain list shows it)
