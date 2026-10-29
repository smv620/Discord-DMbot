"""A kept backstory connection starts as known by the uploader's character (#2935, slice C2b):
the choice rides on the waiting row, Keep passes it on, and Undo puts it back. Invented names."""

from __future__ import annotations

from dmbot.memory import backstory_keep as keep
from dmbot.memory.backstory_links import BackstoryLinks, NewLink, NewWhich
from dmbot.memory.models import CONFIRMED
from tests.test_memory_names import GUILD, NamesTest


class KnownBy(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = BackstoryLinks(self.db)

    async def ids(self) -> dict[str, str]:
        cid, gid = self.campaign.id, GUILD
        add = self.memory.add_entity
        tavin = await add(
            gid, cid, name="Tavin", type="character", role="player_character", played_by=1001,
            status=CONFIRMED, source="dm",
        )  # fmt: skip
        voss = await add(gid, cid, name="Voss", type="npc", status=CONFIRMED, source="dm")
        council = await add(
            gid, cid, name="Lantern Council", type="faction", status=CONFIRMED, source="dm"
        )
        return {"Tavin": tavin.value.id, "Voss": voss.value.id, "Lantern Council": council.value.id}

    async def written(self) -> str:
        (rel,) = [
            r
            for r in await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
            if r.predicate == "member_of"
        ]
        return str(rel.id)

    async def knowers(self, ids: list[str]) -> dict[str, list[str]]:
        return await self.memory.connection_knowers(GUILD, self.campaign.id, ids)

    async def test_the_waiting_row_keeps_the_character_and_keep_all_passes_it_on(self) -> None:
        ids = await self.ids()
        await self.store.add(
            GUILD, self.campaign.id,
            [NewLink(ids["Voss"], "member_of", ids["Lantern Council"], "q", known_by=ids["Tavin"])],
        )  # fmt: skip
        (waiting,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual(waiting.known_by, ids["Tavin"])
        done = await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, [waiting])
        self.assertEqual(len(done.kept), 1)
        rel = await self.written()
        self.assertEqual(await self.knowers([rel]), {rel: [ids["Tavin"]]})

    async def test_no_character_means_only_the_dm_as_before(self) -> None:
        ids = await self.ids()
        await self.store.add(
            GUILD, self.campaign.id,
            [NewLink(ids["Voss"], "member_of", ids["Lantern Council"], "q")],
        )  # fmt: skip
        (waiting,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual(waiting.known_by, "")
        await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, [waiting])
        self.assertEqual(await self.knowers([await self.written()]), {})

    async def test_undo_puts_the_waiting_row_back_with_the_character(self) -> None:
        ids = await self.ids()
        await self.store.add(
            GUILD, self.campaign.id,
            [NewLink(ids["Voss"], "member_of", ids["Lantern Council"], "q", known_by=ids["Tavin"])],
        )  # fmt: skip
        (waiting,) = await self.store.waiting(GUILD, self.campaign.id)
        done = await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, [waiting])
        await keep.undo_all(self.memory, self.store, GUILD, self.campaign.id, done)
        (again,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual(again.known_by, ids["Tavin"])

    async def test_a_which_answer_carries_the_character_to_the_connection(self) -> None:
        ids = await self.ids()
        mara = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Mara", type="npc", status=CONFIRMED, source="dm"
        )
        other = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Mara Vell", type="npc", status=CONFIRMED, source="dm"
        )
        await self.store.add_which(
            GUILD, self.campaign.id,
            [NewWhich(ids["Voss"], True, "member_of", "Mara",
                      (mara.value.id, other.value.id), "q", known_by=ids["Tavin"])],
        )  # fmt: skip
        (question,) = await self.store.which_waiting(GUILD, self.campaign.id)
        await self.store.pick_which(GUILD, self.campaign.id, question.id, mara.value.id)
        (waiting,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual(waiting.known_by, ids["Tavin"])

    async def test_a_secret_name_gets_no_character(self) -> None:
        ids = await self.ids()
        await self.store.add(
            GUILD, self.campaign.id,
            [NewLink(ids["Voss"], "member_of", ids["Lantern Council"], "q", True,
                     known_by=ids["Tavin"])],
        )  # fmt: skip
        (waiting,) = await self.store.waiting(GUILD, self.campaign.id)
        await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, [waiting])
        self.assertEqual(await self.knowers([await self.written()]), {})

    async def test_a_connection_that_exists_keeps_the_characters_already_chosen(self) -> None:
        ids = await self.ids()
        mara = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Mara", type="character", role="player_character",
            played_by=1002, status=CONFIRMED, source="dm",
        )  # fmt: skip
        first = await self.memory.add_relation(
            GUILD, self.campaign.id, ids["Voss"], "member_of", ids["Lantern Council"],
            source="dm", confidence=1.0, status=CONFIRMED, unrevealed=True,
            known_by=[mara.value.id],
        )  # fmt: skip
        again = await self.memory.add_relation(
            GUILD, self.campaign.id, ids["Voss"], "member_of", ids["Lantern Council"],
            source="dm", confidence=1.0, status=CONFIRMED, unrevealed=True,
            known_by=[ids["Tavin"]],
        )  # fmt: skip
        rid = first.value[0].id
        self.assertEqual(again.value[0].id, rid)
        got = await self.knowers([rid])
        self.assertEqual(sorted(got[rid]), sorted([mara.value.id, ids["Tavin"]]))


class SecretNames(NamesTest):
    """The secret names an upload gives start as known by its character too (C2c)."""

    ids = KnownBy.ids

    async def stored(self, known_by: str) -> tuple[str, list[str]]:
        from dmbot.memory import background as bg
        from dmbot.ui import name_background as ui_bg

        found = [bg.Found("Vell Ashgrove", "npc", (), ("Nightshade",), "q")]
        made = await ui_bg.store(self.memory, self.campaign, found, known_by=known_by)
        (entity_id,) = made["npc"]
        aliases = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=entity_id, include_secret=True
        )
        return entity_id, [a.id for a in aliases if a.secret]

    async def test_a_secret_name_from_a_pc_backstory_is_known_by_that_pc(self) -> None:
        ids = await self.ids()
        _, secret = await self.stored(ids["Tavin"])
        got = await self.memory.alias_knowers(GUILD, self.campaign.id, secret)
        self.assertEqual(got, {secret[0]: [ids["Tavin"]]})

    async def test_with_no_character_the_secret_name_is_the_dms_alone(self) -> None:
        await self.ids()
        _, secret = await self.stored("")
        self.assertEqual(await self.memory.alias_knowers(GUILD, self.campaign.id, secret), {})

    async def test_only_the_secret_spellings_and_all_of_them_get_the_character(self) -> None:
        from dmbot.memory import background as bg
        from dmbot.ui import name_background as ui_bg

        ids = await self.ids()
        found = [bg.Found("Vell Ashgrove", "npc", ("Lady Vell",), ("Nightshade", "The Hood"), "q")]
        made = await ui_bg.store(self.memory, self.campaign, found, known_by=ids["Tavin"])
        (entity_id,) = made["npc"]
        aliases = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=entity_id, include_secret=True
        )
        secret = [a.id for a in aliases if a.secret]
        plain = [a.id for a in aliases if not a.secret]
        self.assertEqual(len(secret), 2)
        got = await self.memory.alias_knowers(GUILD, self.campaign.id, secret + plain)
        self.assertEqual(sorted(got), sorted(secret))

    async def test_a_character_that_is_not_one_leaves_the_name_with_the_dm_alone(self) -> None:
        ids = await self.ids()
        _, secret = await self.stored(ids["Voss"])  # (an NPC, not a player character)
        self.assertEqual(await self.memory.alias_knowers(GUILD, self.campaign.id, secret), {})
