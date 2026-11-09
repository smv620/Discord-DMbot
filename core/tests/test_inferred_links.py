"""A connection DMbot inferred from lore (#2959 item 5): it carries its reason from the read to
the waiting row and the kept connection's "Why?", is never family, a pronoun or a "Which one?", and
is never kept by the sure step or Keep all, only by the DM's own press. Invented names."""

from __future__ import annotations

import json
import unittest
from collections import Counter
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.campaigns import CampaignStore
from dmbot.memory import auto_accept, backstory_keep
from dmbot.memory import background as bg
from dmbot.memory.backstory_links import BackstoryLinks, NewLink
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.ui import connection_edit
from dmbot.ui.name_background import inferred_line
from tests.pg import DatabaseTest

TEXT = (
    "Mirelle the Pale and Brannoch both crew the Grey Gull under the same captain. "
    "Brannoch is the brother of Mirelle."
)
WHY = "both crew the Grey Gull under the same captain"


def reply(*links: dict[str, str]) -> str:
    names: list[dict[str, Any]] = [
        {"name": n, "kind": "person", "also": [], "secret": [], "quote": n}
        for n in ("Mirelle the Pale", "Brannoch")
    ]
    return json.dumps({"names": names, "connections": list(links)})


def link(how: str, quote: str, inferred: str = "", words: str = "") -> dict[str, str]:
    return {"a": "Mirelle the Pale", "how": how, "b": "Brannoch", "quote": quote,
            "words": words, "inferred": inferred}  # fmt: skip


class TheRead(unittest.TestCase):
    def test_an_inferred_line_keeps_its_reason_and_a_stated_one_has_none(self) -> None:
        quote = "Mirelle the Pale and Brannoch both crew the Grey Gull"
        (got,) = bg.read_connections(reply(link("knows", quote, WHY)), TEXT)
        self.assertEqual(got.inferred, WHY)
        (plain,) = bg.read_connections(reply(link("knows", quote)), TEXT)
        self.assertEqual(plain.inferred, "")

    def test_family_is_never_an_inference(self) -> None:
        lost: Counter[str] = Counter()
        got = bg.read_connections(
            reply(link("sibling", "Brannoch is the brother of Mirelle", "they look alike")),
            TEXT,
            dropped=lost,
        )
        self.assertEqual((got, lost["inferred_family"]), ([], 1))

    def test_a_long_reason_is_cut_at_a_word(self) -> None:
        reason = bg.inferred_reason("word " * 40)
        self.assertLessEqual(len(reason), bg.INFERRED_CHARS)
        self.assertFalse(reason.endswith(" "))
        self.assertEqual(bg.inferred_reason(None), "")


def waiting(inferred: str = "") -> Any:
    return SimpleNamespace(
        subject_id="s", predicate="knows", object_id="o", secret=False, detail="", truth=False,
        decided="", known_by="", world=0, upload="", inferred=inferred, quote="q", ready=True,
    )  # fmt: skip


class NeverKeptUnasked(unittest.IsolatedAsyncioTestCase):
    async def test_keep_all_leaves_an_inference_waiting_and_one_press_keeps_it(self) -> None:
        memory = SimpleNamespace(ontology=AsyncMock(return_value=SimpleNamespace(predicates={})))
        with patch.object(backstory_keep, "shown_entry_ids", AsyncMock(return_value=set())):
            done = await backstory_keep.keep_all(memory, SimpleNamespace(), 1, "c", [waiting("x")])  # type: ignore[arg-type]
        self.assertEqual((done.kept, done.refused), ([], 0))
        memory.ontology.assert_not_awaited()  # (nothing was left to keep: nothing was read)

    async def test_the_sure_step_counts_an_inference_as_waiting(self) -> None:
        links = SimpleNamespace(waiting=AsyncMock(return_value=[waiting("x"), waiting()]))
        memory = SimpleNamespace(ontology=AsyncMock(return_value=SimpleNamespace(predicates={})))
        tally = auto_accept.Tally()
        kept = AsyncMock(return_value=backstory_keep.KeptAll())
        with patch.object(backstory_keep, "keep_all", kept):
            await auto_accept.accept_connections(memory, links, 1, "c", tally)  # type: ignore[arg-type]
        call = kept.await_args
        assert call is not None
        (given,) = call.args[4:5]
        self.assertEqual([w.inferred for w in given], [""])
        self.assertEqual(tally.waiting[auto_accept.CONNECTIONS], 1)


class TheWords(unittest.IsolatedAsyncioTestCase):
    def test_the_card_says_dmbot_inferred_it(self) -> None:
        self.assertEqual(
            inferred_line(WHY),
            f"🤔 **Inferred by DMbot:** {WHY} (your text doesn't say it outright)\n",
        )
        self.assertEqual(inferred_line(""), "")

    async def test_the_why_never_reads_an_inference_as_the_dm_s_text(self) -> None:
        memory = SimpleNamespace(link_source=AsyncMock(return_value=("Notes, 9 Nov", "q", WHY)))
        found: Any = SimpleNamespace(
            secrets=True, campaign=SimpleNamespace(guild_id=1, id="c"), memory=memory,
            relation=SimpleNamespace(id="r"),
        )  # fmt: skip
        self.assertEqual(
            await connection_edit.why_line(found),
            f'Why: DMbot inferred this from your lore "Notes, 9 Nov": {WHY}\n',
        )


GUILD, DM = 1, 7


class TheStore(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db)
        self.links = BackstoryLinks(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)

    async def person(self, name: str) -> str:
        written = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name=name, source=DM_SOURCE,
            status=CONFIRMED,
        )  # fmt: skip
        return written.value.id

    async def test_the_reason_waits_with_the_row_and_rides_to_the_kept_source(self) -> None:
        cid = self.campaign.id
        a, b = await self.person("Mirelle the Pale"), await self.person("Brannoch")
        await self.links.add(
            GUILD, cid, [NewLink(a, "knows", b, "both crew the Grey Gull", upload="Notes, 9 Nov",
                                 inferred=WHY)],
        )  # fmt: skip
        (row,) = await self.links.waiting(GUILD, cid)
        self.assertEqual(row.inferred, WHY)
        done = await backstory_keep.keep_all(self.memory, self.links, GUILD, cid, [row])
        self.assertEqual(done.kept, [])  # (Keep all leaves it)
        done = await backstory_keep.keep_all(
            self.memory, self.links, GUILD, cid, [row], one_press=True
        )
        ((made, _batch),) = done.kept
        got = await self.memory.link_source(GUILD, cid, made.id)
        assert got is not None
        self.assertEqual(got[2], WHY)


if __name__ == "__main__":
    unittest.main()
