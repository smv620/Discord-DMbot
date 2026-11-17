"""What a map shows outright, as layer rows (#4259, MapBot slice 8a; docs/design/mapbot_layers.md
§5.2, §7.1 to 7.4; Supervisor's ruling on #4259): the read's `shows` (a legend, a labelled border, a
colour key or a terrain symbol only), the words each layer allows, kept places and rulers only, and
the store's write (`source = 'map'`, the map's id, the map above the text, the DM's word above the
map, one batch with the pins). Invented names. The last class needs Postgres."""

from __future__ import annotations

import json
import unittest
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot import images
from dmbot.maps import layers_read
from dmbot.memory import map_facts as mf
from dmbot.memory.models import name_key
from tests.test_map_pins import PinsBase, close_answer
from tests.test_map_store import GUILD_A


def shown(place: str, layer: str, value: str, by: str = "legend") -> images.Shown:
    return images.Shown(place, layer, value, by)


class TheRead(unittest.TestCase):
    def test_only_a_named_place_from_a_legend_border_key_or_symbol(self) -> None:
        raw = json.loads(close_answer())
        raw["shows"] = [
            {"place": "Targos", "layer": "terrain", "value": "Arctic", "from": "symbol"},
            {"place": "Targos", "layer": "control", "value": "Ten-Towns", "from": "banner"},
            {"place": "Luskan", "layer": "terrain", "value": "coastal", "from": "legend"},
            {"place": "Targos", "layer": "weather", "value": "snow", "from": "legend"},
            {"place": "Bryn Shander", "layer": "culture", "value": "", "from": "colour_key"},
        ]
        read = images.parse_close_map(json.dumps(raw))
        assert read is not None
        self.assertEqual(read.shows, (shown("Targos", "terrain", "Arctic", "symbol"),))

    def test_an_older_answer_without_it_shows_nothing(self) -> None:
        read = images.parse_close_map(close_answer())
        assert read is not None
        self.assertEqual(read.shows, ())


PLACES: dict[str, str | None] = {
    name_key("Targos"): "t", name_key("Bryn Shander"): "b", name_key("Twin"): None,
}  # fmt: skip
RULERS: dict[str, str | None] = {name_key("Ten-Towns Council"): "c", name_key("Targos"): "t"}


class TheWords(unittest.TestCase):
    def test_each_layers_own_words(self) -> None:
        got = layers_read.reads(
            [
                shown("Targos", "terrain", "Arctic"), shown("Targos", "climate", "cold"),
                shown("Bryn Shander", "culture", "Elven"),
                shown("Bryn Shander", "population", "dwarves"),
                shown("Bryn Shander", "control", "Ten-Towns Council"),
            ],
            PLACES, RULERS,
        )  # fmt: skip
        self.assertEqual(
            [(r.place_id, r.layer, r.value, r.how) for r in got],
            [
                ("t", mf.TERRAIN, "arctic", ""), ("t", mf.CLIMATE, "cold", ""),
                ("b", mf.CULTURE, "elf", layers_read.MAP_CULTURE_HOW),
                ("b", mf.POPULATION, "dwarf", ""), ("b", mf.CONTROL, "c", "rules"),
            ],
        )  # fmt: skip

    def test_not_sure_means_nothing(self) -> None:
        got = layers_read.reads(
            [
                shown("Targos", "terrain", "icy wastes"),  # (not a word on the list)
                shown("Twin", "terrain", "hill"),  # (a name that is two places)
                shown("Luskan", "terrain", "coastal"),  # (not a kept place)
                shown("Targos", "control", "the Black Banner"),  # (no kept group by that name)
                shown("Targos", "control", "Targos"),  # (a place never rules itself)
                shown("Targos", "terrain", "arctic"), shown("Targos", "terrain", "Arctic"),
            ],
            PLACES, RULERS,
        )  # fmt: skip
        self.assertEqual([(r.layer, r.value) for r in got], [(mf.TERRAIN, "arctic")])


class TheWrite(PinsBase):  # (Postgres: Targos is a kept place; the map is the DM's)
    async def record(self, **more: Any) -> Any:
        read = images.parse_close_map(close_answer(**more))
        assert read is not None
        return await self.maps.record_positions(
            GUILD_A, self.one.id, self.map_id, read, layers=True
        )

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT layer, value, status, source, also_text, map_id, to_session_at"
                " FROM memory_map_facts WHERE entity_id = %s ORDER BY layer, value",
                (self.targos,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def test_written_as_the_maps_read(self) -> None:
        got = await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "symbol"}
        ])  # fmt: skip
        self.assertEqual(got.written, {mf.TERRAIN: 1})
        (row,) = await self.rows()
        self.assertEqual(
            (row["value"], row["status"], row["source"], row["also_text"], row["map_id"]),
            ("arctic", mf.READ, mf.FROM_MAP, False, self.map_id),
        )

    async def test_switched_off_writes_nothing(self) -> None:
        read = images.parse_close_map(close_answer(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "symbol"}
        ]))  # fmt: skip
        assert read is not None
        await self.maps.record_positions(GUILD_A, self.one.id, self.map_id, read)
        self.assertEqual(await self.rows(), [])

    async def test_the_same_word_the_text_read_becomes_the_maps(self) -> None:
        from dmbot.memory.map_fact_store import Read

        await self.memory.add_read_map_facts(
            GUILD_A, self.one.id, [Read(self.targos, mf.TERRAIN, "arctic")], source="extractor"
        )
        got = await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "legend"}
        ])  # fmt: skip
        self.assertEqual(got.written, {})  # (nothing new: no note, no Undo for it)
        (row,) = await self.rows()  # (one row, never two)
        self.assertEqual(
            (row["source"], row["also_text"], row["map_id"]), (mf.FROM_MAP, True, self.map_id)
        )

    async def test_the_map_corrects_the_texts_read_but_never_the_dms_word(self) -> None:
        from dmbot.memory.map_fact_store import Read

        await self.memory.add_read_map_facts(
            GUILD_A, self.one.id, [Read(self.targos, mf.TERRAIN, "forest")], source="extractor"
        )
        await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "symbol"}
        ])  # fmt: skip
        self.assertEqual(
            [(r["value"], r["status"], r["source"]) for r in await self.rows()],
            [("arctic", mf.READ, mf.FROM_MAP), ("forest", mf.REJECTED, mf.FROM_TEXT)],
        )
        await self.memory.set_map_fact(
            GUILD_A, self.one.id, self.targos, mf.TERRAIN, "hill", source="dm"
        )
        got = await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "mountain", "from": "symbol"}
        ])  # fmt: skip
        self.assertEqual(got.layers.get("noted"), 1)  # (against the DM's word: World notes)
        self.assertNotIn("mountain", [r["value"] for r in await self.rows()])

    async def test_the_notes_undo_takes_the_rows_and_leaves_the_pins(self) -> None:
        # (Supervisor's ruling on #4699, #4700: Undo takes back exactly what the note reports)
        got = await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "symbol"}
        ])  # fmt: skip
        self.assertEqual(got.written, {mf.TERRAIN: 1})
        pins = await self.features()
        self.assertTrue(pins)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT table_name, batch FROM memory_changes ORDER BY batch"
            )
            batches = [(r["table_name"], r["batch"]) for r in await cur.fetchall()]
        self.assertIn(("memory_map_facts", got.batch), batches)
        self.assertNotIn(("map_features", got.batch), batches)  # (the pins: a batch of their own)
        await self.memory.undo(GUILD_A, self.one.id, got.batch)
        self.assertEqual(await self.rows(), [])
        self.assertEqual(await self.features(), pins)

    async def test_the_pins_undo_leaves_the_rows(self) -> None:
        got = await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "symbol"}
        ])  # fmt: skip
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT min(batch) AS b FROM memory_changes WHERE table_name = 'map_features'"
            )
            pins = (await cur.fetchone() or {}).get("b")
        assert pins is not None and pins != got.batch
        await self.memory.undo(GUILD_A, self.one.id, int(pins))
        self.assertEqual(await self.features(), [])
        self.assertEqual(len(await self.rows()), 1)

    async def test_a_row_starts_at_the_session_the_picture_was_posted_in(self) -> None:
        # (§7.4: the session that began last before the picture was posted, not before it was
        # kept as a map; a session after it doesn't count)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT p.made_at FROM campaign_pictures p JOIN campaign_maps m"
                " ON p.id = m.picture_id WHERE m.id = %s",
                (self.map_id,),
            )
            made = int((await cur.fetchone() or {})["made_at"])
            for at in (made - 100, made + 100):
                await conn.execute(
                    "INSERT INTO transcript_sessions (guild_id, campaign_id, id, started_at)"
                    " VALUES (%s, %s, %s, %s)",
                    (GUILD_A, self.one.id, uuid.uuid4().hex, at),
                )
        await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "symbol"}
        ])  # fmt: skip
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT from_session_at FROM memory_map_facts WHERE entity_id = %s", (self.targos,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["from_session_at"], made - 100)

    async def test_a_row_the_text_says_after_the_read_stops_its_undo(self) -> None:
        from dmbot.memory import _changes
        from dmbot.memory.map_fact_store import Read
        from dmbot.memory.models import MemoryRuleError

        got = await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "symbol"}
        ])  # fmt: skip
        await self.memory.add_read_map_facts(  # (the session's reader says it too, later)
            GUILD_A, self.one.id, [Read(self.targos, mf.TERRAIN, "arctic")], source="extractor"
        )
        with self.assertRaises(MemoryRuleError) as caught:
            await self.memory.undo(GUILD_A, self.one.id, got.batch)
        self.assertEqual(str(caught.exception), _changes.CHANGED_SINCE)  # (the note: "changed")


class TheNote(unittest.IsolatedAsyncioTestCase):
    """The DM's private note (#4259 8a; the ux-critic's words; Supervisor's timing ruling)."""

    def test_the_label(self) -> None:
        from dmbot.ui.map_layers_note import map_label

        self.assertEqual(map_label("The Ten-Towns", 0), "the Ten-Towns map")
        self.assertEqual(map_label("Icewind Dale Map", 0), "the Icewind Dale Map")
        self.assertEqual(map_label("Harbour plan", 0), "the Harbour plan")
        self.assertEqual(map_label("", 1_763_078_400), "a map posted on 14 Nov")

    def test_the_words(self) -> None:
        from dmbot.memory import map_facts as mf
        from dmbot.ui.map_layers_note import note_text

        written = {mf.TERRAIN: 3, mf.CONTROL: 1, mf.CULTURE: 2}
        self.assertEqual(
            note_text("the Ten-Towns map", written, {}, "https://example.org"),
            "🗺️ DMbot read the Ten-Towns map: land for 3 places, ruler for 1, culture for 2."
            " They're waiting in [🌍 World notes](<https://example.org/account/names>) for you"
            " to check.",
        )
        self.assertEqual(
            note_text(
                "the Ten-Towns map", {mf.CLIMATE: 1, mf.POPULATION: 2},
                {"corrected": 1, "noted": 1},
            ),
            "🗺️ DMbot read the Ten-Towns map: climate for 1 place, who lives there for 2. They're"
            " waiting in 🌍 World notes for you to check. 1 of them replaces what DMbot read in"
            " your lore. 1 differs from what's already there, and that stays as it is.",
        )  # fmt: skip
        # (a difference alone writes no row: nothing to check or undo, so no note)
        self.assertIsNone(note_text("the Ten-Towns map", {}, {"noted": 1}))
        self.assertIsNone(note_text("the Ten-Towns map", {}, {"added": 0}))

    def test_undo_only_when_something_was_written(self) -> None:
        from dmbot.ui.map_layers_note import UNDO, note_view

        self.assertIsNone(note_view(1, "c" * 32, None, "x"))
        view = note_view(1, "c" * 32, 5, "x")
        labels = [getattr(getattr(i, "item", i), "label", None) for i in view.children]
        self.assertEqual(labels, [UNDO])  # (one button; World notes is a link in the words)

    def press(self, user: int, undo: Any) -> tuple[Any, Any]:
        camp = SimpleNamespace(dm_user_ids=frozenset({7}))
        followup = AsyncMock()
        interaction = SimpleNamespace(
            client=SimpleNamespace(
                campaigns=SimpleNamespace(get=AsyncMock(return_value=camp)),
                memory=SimpleNamespace(undo=undo),
            ),
            user=SimpleNamespace(id=user),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=followup),
            message=SimpleNamespace(content="🗺️ DMbot read the Ten-Towns map: land for 1 place."),
            edit_original_response=AsyncMock(),
        )
        return interaction, followup

    async def test_undo_for_a_dm_only_and_its_words(self) -> None:
        from dmbot.memory.models import MemoryRuleError, TooLateToUndo
        from dmbot.ui import map_layers_note as note

        button = note.UndoMapLayersButton(1, "c" * 32, 5)
        undo = AsyncMock()
        interaction, _ = self.press(9, undo)
        await button.callback(interaction)
        undo.assert_not_awaited()
        self.assertEqual(interaction.response.send_message.await_args.args[0], note.ONLY_DM)
        interaction, _ = self.press(7, undo)
        await button.callback(interaction)
        undo.assert_awaited_once_with(1, "c" * 32, 5)
        self.assertEqual(
            interaction.edit_original_response.await_args.kwargs["content"],
            "↩ Undone. Nothing from the Ten-Towns map is in World notes now. The map itself is"
            " still kept.",
        )
        from dmbot.memory import _changes

        for error, words in (
            (TooLateToUndo("x"), note.TOO_OLD),
            (MemoryRuleError(_changes.ALREADY_UNDONE), note.ALREADY),
            (MemoryRuleError(_changes.CHANGED_SINCE), note.NOT_UNDONE),
            (MemoryRuleError(_changes.MAP_PICTURE_GONE), note.PICTURE_GONE),
        ):
            interaction, followup = self.press(7, AsyncMock(side_effect=error))
            await button.callback(interaction)
            self.assertEqual(followup.await_args.args[0], words)


class TheTiming(unittest.IsolatedAsyncioTestCase):
    """At once with no session running for the campaign; held for its end when one is; once."""

    def make(self, running: str | None) -> Any:
        from dmbot.memory import map_facts as mf

        a_map = SimpleNamespace(title="Ten-Towns", created_at=0)
        bot = SimpleNamespace(
            map_store=SimpleNamespace(get=AsyncMock(return_value=a_map)),
            campaigns=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(dm_user_ids=frozenset({7, 8})))
            ),
            settings=SimpleNamespace(site_url=""),
            tables={1: SimpleNamespace(guild_id=1, campaign_id=running, held_map_notes=[])}
            if running
            else {},
            _dm_message=AsyncMock(),
        )
        bot.got = SimpleNamespace(written={mf.TERRAIN: 1}, layers={"added": 1}, batch=4)
        return bot

    async def test_at_once_to_each_dm_with_no_session(self) -> None:
        from dmbot.bot import _map_layers_note

        bot = self.make(None)
        await _map_layers_note(bot, 1, "c" * 32, "m", bot.got)
        self.assertEqual([c.args[0] for c in bot._dm_message.await_args_list], [7, 8])

    async def test_held_during_the_session_and_sent_once_after(self) -> None:
        from dmbot.bot import DMBot, _map_layers_note

        bot = self.make("c" * 32)
        await _map_layers_note(bot, 1, "c" * 32, "m", bot.got)
        bot._dm_message.assert_not_awaited()
        table = bot.tables[1]
        await DMBot.send_held_map_notes(bot, table)
        self.assertEqual(bot._dm_message.await_count, 2)
        await DMBot.send_held_map_notes(bot, table)
        self.assertEqual(bot._dm_message.await_count, 2)  # (once)

    def test_a_title_with_a_colon_comes_back_whole(self) -> None:
        from dmbot.ui.map_layers_note import _label_in

        self.assertEqual(
            _label_in("🗺️ DMbot read the Icewind Dale: Ten-Towns map: ruler for 1 place."),
            "the Icewind Dale: Ten-Towns map",
        )

    async def test_another_campaigns_session_does_not_hold_it(self) -> None:
        from dmbot.bot import _map_layers_note

        bot = self.make("d" * 32)
        await _map_layers_note(bot, 1, "c" * 32, "m", bot.got)
        self.assertEqual(bot._dm_message.await_count, 2)
