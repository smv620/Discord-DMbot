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


class ARead(Acting):
    """#4356: a place's size or kind DMbot only read reaches the DM's browser only; once the DM
    has said it, everyone who sees the name gets it."""

    async def read_by_dmbot(self) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_entities SET place_level = 'settlement', place_level_from = 'read',"
                " form = 'water', form_from = 'read' WHERE campaign_id = %s AND id = %s",
                (self.one.id, self.keep),
            )

    async def row(self, who: object = None) -> dict[str, object]:
        got = await self.page(who)  # type: ignore[arg-type]
        return dict(next(n for n in got["names"] if n["id"] == self.keep))

    async def test_a_read_value_is_the_dms_alone(self) -> None:
        await self.read_by_dmbot()
        fields = ("placeLevel", "placeLevelFrom", "form", "formFrom")
        dm_row = await self.row()
        self.assertEqual([dm_row[f] for f in fields], ["settlement", "read", "water", "read"])
        bob = await self.signed(BOB, THURSDAY)
        player_row = await self.row(bob)
        self.assertEqual([player_row.get(f) for f in fields], [None, None, None, None])

    async def test_once_the_dm_says_it_everyone_gets_it(self) -> None:
        await self.read_by_dmbot()
        await self.memory.set_place_form(self.g, self.one.id, self.keep, "water", source="dm")
        player_row = await self.row(await self.signed(BOB, THURSDAY))
        self.assertEqual((player_row["form"], player_row["formFrom"]), ("water", "dm"))
        self.assertIsNone(player_row.get("placeLevel"))  # (the size is still only DMbot's read)
