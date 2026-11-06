"""PlotBot's starting points by meaning (#2664): a question that describes someone without naming
them finds their card; nothing is sent when the switch is off; a failure leaves the names alone;
nothing from another campaign; and the sidebar adds the leads to what the question spelled out.
A fake Voyage that puts words about the same thing at the same point. Invented names."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from dmbot.ai import AIOutOfFunds
from dmbot.memory import meaning as mn
from dmbot.memory import vectors as vs
from dmbot.memory.embedding import pieces_of
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.voyage import ProviderError, Ranked
from dmbot.sidebar import answer as sidebar
from tests.pg import DatabaseTest
from tests.sidebar_brevity_cases import reply
from tests.test_memory_lookup import data
from tests.test_sidebar_answer import FakeAI, campaign
from tests.test_sidebar_recall import INDEX, recalled

GUILD, DM = 1, 7
MODEL = "voyage-3.5"
# words about one thing sit at one point, as a real model would put them near each other
TOPICS = {
    ("poison", "hemlock", "soup", "cook"): [1.0, 0.0, 0.0],
    ("ship", "sail", "harbour", "captain"): [0.0, 1.0, 0.0],
    ("dragon", "wyrm", "hoard"): [0.0, 0.0, 1.0],
}


def point(text: str) -> list[float]:
    out = [0.05, 0.05, 0.05]
    for words, vector in TOPICS.items():
        if any(w in text.lower() for w in words):
            out = [a + b for a, b in zip(out, vector, strict=True)]
    return out


class FakeVoyage:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.embedded: list[str] = []
        self.reranked: list[tuple[str, list[str]]] = []
        self.fail = fail

    async def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        if self.fail:
            raise self.fail
        self.embedded += texts
        return [point(t) for t in texts]

    async def rerank(self, query: str, documents: Sequence[str], *, top_k: int) -> list[Ranked]:
        if self.fail:
            raise self.fail
        self.reranked.append((query, list(documents)))
        score = [sum(a * b for a, b in zip(point(query), point(d), strict=True)) for d in documents]
        order = sorted(range(len(documents)), key=lambda i: -score[i])[:top_k]
        return [Ranked(i, min(score[i] / 2, 1.0)) for i in order]


class Finding(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.campaigns import CampaignStore

        self.campaigns = CampaignStore(self.db)
        self.store = vs.VectorStore(self.db)
        self.c = (await self.campaigns.create(GUILD, "Frostmaiden", DM)).id
        self.on = True
        self.counted = 0
        self.ids: dict[str, str] = {}
        for n, (name, desc) in enumerate(
            [
                ("Bram", "The cook who put hemlock in the baron's soup"),
                ("Captain Ro", "Sails the old ship out of the harbour"),
                ("Vyrax", "A wyrm asleep on its hoard"),
            ]
        ):
            await self.add_entry(f"{n + 1:032x}", name, desc)

    async def add_entry(
        self, id_: str, name: str, desc: str, *, campaign: str | None = None, **flags: object
    ) -> None:
        cid = campaign or self.c
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at, hidden_from_players, audience_limited) VALUES"
                " (%s, %s, %s, %s, %s, %s, 'confirmed', 'dm', 1, %s, %s)",
                (
                    GUILD, cid, id_, flags.get("type", "character"), name, desc,
                    bool(flags.get("hidden", False)), bool(flags.get("audience")),
                ),
            )  # fmt: skip
            if flags.get("alias"):
                await conn.execute(
                    "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key,"
                    " kind, secret, status, sound_codes, source, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, 'full', true, 'confirmed', '{}', 'dm', 1)",
                    (GUILD, cid, f"{id_[:-1]}f", id_, name, name.lower()),
                )
        self.ids[name] = id_
        piece = pieces_of(vs.ASSERTION, id_, f"{name}: {desc}")[0]
        await self.store.save(
            GUILD, cid, MODEL, [vs.Item(vs.ASSERTION, id_, piece.hash, point(piece.text))]
        )

    async def allowed(self) -> bool:
        return self.on

    async def counter(self) -> None:
        self.counted += 1

    def seeds(self, voyage: FakeVoyage) -> mn.MeaningSeeds:
        return mn.MeaningSeeds(
            self.db, self.store, voyage, MODEL, allowed=self.allowed, count_call=self.counter
        )

    async def test_a_question_that_describes_someone_finds_their_card(self) -> None:
        voyage = FakeVoyage()
        found = await self.seeds(voyage).find(GUILD, self.c, "who poisoned the baron's dinner?")
        self.assertEqual(found, [self.ids["Bram"]])
        self.assertEqual(self.counted, 2)  # the question, then the reranker
        self.assertEqual(voyage.embedded, ["who poisoned the baron's dinner?"])

    async def test_nothing_close_enough_is_nothing_not_the_least_bad_guess(self) -> None:
        self.assertEqual(await self.seeds(FakeVoyage()).find(GUILD, self.c, "what time is it"), [])

    async def test_nothing_is_sent_when_the_switch_is_off(self) -> None:
        self.on = False
        voyage = FakeVoyage()
        self.assertEqual(await self.seeds(voyage).find(GUILD, self.c, "who put poison in it"), [])
        self.assertEqual((voyage.embedded, voyage.reranked, self.counted), ([], [], 0))

    async def test_a_failure_or_no_credit_leaves_the_names_alone(self) -> None:
        for error in (ProviderError("busy", 503), AIOutOfFunds("none")):
            with (
                self.subTest(error=type(error).__name__),
                self.assertLogs("dmbot.memory.meaning", "WARNING") as logged,
            ):
                found = await self.seeds(FakeVoyage(fail=error)).find(GUILD, self.c, "poison?")
            self.assertEqual(found, [])
            self.assertNotIn("poison", "".join(logged.output))

    async def test_a_campaign_with_no_numbers_yet_finds_nothing(self) -> None:
        other = (await self.campaigns.create(GUILD, "Sunken City", DM)).id
        self.assertEqual(
            await self.seeds(FakeVoyage()).find(GUILD, other, "poison in the soup"), []
        )

    async def test_another_campaigns_entry_is_never_found(self) -> None:
        other = (await self.campaigns.create(GUILD, "Sunken City", DM)).id
        await self.add_entry("f" * 32, "Elsewhere", "A cook with hemlock", campaign=other)
        found = await self.seeds(FakeVoyage()).find(GUILD, self.c, "the hemlock cook")
        self.assertNotIn("f" * 32, found)
        self.assertEqual(found, [self.ids["Bram"]])

    async def test_for_players_leaves_out_an_entry_they_may_not_know_yet(self) -> None:
        await self.add_entry("e" * 32, "Hidden cook", "A cook with hemlock soup", hidden=True)
        both = await self.seeds(FakeVoyage()).find(GUILD, self.c, "the hemlock cook", keep=5)
        self.assertIn("e" * 32, both)
        safe = await self.seeds(FakeVoyage()).find(
            GUILD, self.c, "the hemlock cook", keep=5, for_players=True
        )
        self.assertNotIn("e" * 32, safe)
        self.assertIn(self.ids["Bram"], safe)

    async def test_for_players_matches_the_websites_rules_each_case_live(self) -> None:
        cases: dict[str, dict[str, Any]] = {
            "secret name": dict(alias="Cook"),
            "plot note": dict(type="thread"),
            "quest": dict(type="quest"),
            "a person's card": dict(type="person"),
            "clue some know": dict(audience="x"),
            "hidden": dict(hidden=True),
        }
        for label, flags in cases.items():
            with self.subTest(label):
                name = f"Cook {label}"
                id_ = f"{abs(hash(label)) % 10**16:032x}"
                await self.add_entry(id_, name, "A cook with hemlock soup", **flags)
                dm = await self.seeds(FakeVoyage()).find(GUILD, self.c, "hemlock cook", keep=20)
                players = await self.seeds(FakeVoyage()).find(
                    GUILD, self.c, "hemlock cook", keep=20, for_players=True
                )
                self.assertIn(id_, dm)  # the DM's own search still finds it
                self.assertNotIn(id_, players)

    async def test_the_rule_is_read_live_not_from_when_the_numbers_were_made(self) -> None:
        bram = self.ids["Bram"]
        before = await self.seeds(FakeVoyage()).find(
            GUILD, self.c, "hemlock cook", for_players=True
        )
        self.assertEqual(before, [bram])
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET hidden_from_players = true WHERE id = %s", (bram,)
            )
        after = await self.seeds(FakeVoyage()).find(GUILD, self.c, "hemlock cook", for_players=True)
        self.assertEqual(after, [])  # no new numbers were made: the filter is the change

    async def test_an_entry_merged_away_since_is_not_a_lead(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET merged_into = %s, status = 'merged' WHERE id = %s",
                (self.ids["Vyrax"], self.ids["Bram"]),
            )
        self.assertEqual(await self.seeds(FakeVoyage()).find(GUILD, self.c, "the hemlock cook"), [])

    async def test_a_long_question_is_cut_and_a_long_entry_found_by_its_first_part(self) -> None:
        voyage = FakeVoyage()
        await self.seeds(voyage).find(GUILD, self.c, "poison " * 500)
        self.assertLessEqual(len(voyage.embedded[0]), mn.QUESTION_MAX)


class Drift(DatabaseTest):
    def test_the_plot_note_kinds_are_the_websites(self) -> None:
        """The leads' filter and the website's reading doors hide the same kinds from members; if
        one list changes and not the other, a player-facing answer could start from a plot note."""
        import re

        from dmbot import schema

        source = "\n".join(sql for _, sql in schema.MIGRATIONS)  # (the SQL the migrations run)
        found = {
            tuple(re.findall(r"'(\w+)'", m))
            for m in re.findall(r"e\.type NOT IN \(([^)]*)\)", source)
            if "'thread'" in m  # (the lists that hide the plot notes; others hide other kinds)
        }
        self.assertTrue(found)
        self.assertEqual(found, {tuple(mn.PLAYER_HIDDEN_TYPES)})


class InTheSidebar(DatabaseTest):
    """The leads are added to the names the question spelled out; the answer never waits on them."""

    def engine(self, meaning: object, seen: list[list[str]]) -> sidebar.Sidebar:
        async def gate(c: object, user: int) -> None:
            return None

        async def houses(c: object) -> list[object]:
            return []

        async def names(c: object) -> CampaignLookup:
            return CampaignLookup.build(data())

        async def recaller(c: object, seeds: Sequence[str]) -> object:
            seen.append(list(seeds))
            return recalled()

        return sidebar.Sidebar(
            FakeAI([reply("Bram.", "campaign names")]),
            INDEX,
            gate=gate,
            houses=houses,  # type: ignore[arg-type]
            names=names,
            recall=recaller,  # type: ignore[arg-type]
            meaning=meaning,  # type: ignore[arg-type]
            clock=lambda: 0.0,
        )

    async def test_a_question_with_no_names_still_starts_the_recall_by_meaning(self) -> None:
        seen: list[list[str]] = []

        async def by_meaning(c: object, question: str, asker: int) -> list[str]:
            return ["bram-id"]

        await self.engine(by_meaning, seen).answer(
            campaign(), "who poisoned the dinner", asker_id=7
        )
        self.assertEqual(seen, [["bram-id"]])

    async def test_leads_come_after_the_spelled_out_names_without_repeats(self) -> None:
        seen: list[list[str]] = []
        from tests.test_memory_lookup import BELLEROS

        async def by_meaning(c: object, question: str, asker: int) -> list[str]:
            return ["bram-id", BELLEROS]

        await self.engine(by_meaning, seen).answer(
            campaign(), "what about Belleros and the poisoner", asker_id=7
        )
        self.assertEqual(seen[0][0], BELLEROS)
        self.assertEqual(seen[0].count(BELLEROS), 1)
        self.assertIn("bram-id", seen[0])

    async def test_a_failing_lead_search_never_stops_the_answer(self) -> None:
        seen: list[list[str]] = []

        async def broken(c: object, question: str, asker: int) -> list[str]:
            raise RuntimeError("down")

        got = await self.engine(broken, seen).answer(campaign(), "what about Belleros", asker_id=7)
        self.assertTrue(got.text)
        self.assertEqual(len(seen), 1)

    async def test_a_question_that_already_names_enough_asks_for_no_leads(self) -> None:
        from unittest.mock import patch

        seen: list[list[str]] = []
        asked: list[str] = []

        async def by_meaning(c: object, question: str, asker: int) -> list[str]:
            asked.append(question)
            return []

        with patch.object(sidebar, "ENOUGH_SEEDS", 2):
            await self.engine(by_meaning, seen).answer(
                campaign(), "what about Belleros and Cerric", asker_id=7
            )
        self.assertEqual(asked, [])
        self.assertEqual(len(seen[0]), 2)  # the two names went to the recall as they were

    async def test_a_table_question_never_asks_for_leads(self) -> None:
        seen: list[list[str]] = []
        asked: list[str] = []

        async def by_meaning(c: object, question: str, asker: int) -> list[str]:
            asked.append(question)
            return ["bram-id"]

        await self.engine(by_meaning, seen).answer(
            campaign(), "who poisoned the dinner", asker_id=7, table=True
        )
        self.assertEqual((asked, seen), ([], []))
