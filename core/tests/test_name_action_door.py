"""The website's name-action door keeps every action it ever took (#2447, found in the review of
the pin migration): a migration that rebuilds the door from older text silently drops what a
migration in between added, so the check is on the migrated database, not on one migration."""

from __future__ import annotations

import re

from tests.test_memory_store import MemoryTest

EVERY_ACTION = (
    "spell", "kind", "add-other", "remove-other", "secret", "hide", "merge", "merge-many",
    "remove", "link-add", "link-words", "link-secret", "link-remove", "link-pin",
    "claim-confirm", "claim-reject", "backstory-keep", "backstory-skip", "backstory-keep-all",
    "backstory-which-pick", "backstory-which-neither",
    "reading-place", "reading-no", "reading-undo", "names-fixed-undo",
    "name", "name-join", "name-undo",  # "Not named yet" on the Names page (#2803)
    "link-who", "alias-who",  # "Who knows this?" (#2999)
    "confirm-kinds", "confirm-undo",  # "Add all" for an upload's names (#3207)
    "claim-pick", "claim-unpick",  # "Which one?" for a kept fact (#3463)
    "claim-undo",  # takes back an All true / All secret press (#3645)
    "spelling-yes", "spelling-no",  # "Is this the same place?" (#3533)
    "players-line",  # the line for the players (#3568)
    "offer-clear",  # lift a Never on words (#3577)
    "told-add", "told-remove",  # "Tell a character" (#3542)
    "clock-set", "clock-skip",  # the import clock offer (#3199)
    "match-confirm", "match-undo",  # the answer to one of DMbot's own matches (#3602)
    "link-undo",  # the page's Undo of a connection edit (#4156)
    "met-name", "met-join", "met-remove",  # "Met but not named" (#4084)
    "link-direction",  # A → B, B → A or A ↔ B (#4154)
    "link-right",  # ✅ That's right on an assumed parent (#4304)
    "place-size",  # how big a place is (#4173)
    "place-setting",  # the campaign's setting (#4173 slice 2)
    "place-form",  # land, water or a road (#4173 slice 6a)
    "maps-use", "maps-show", "maps-remove", "maps-takeback", "maps-undo",  # the Maps page (#4347)
    "map-terrain", "map-climate", "map-control", "map-show",  # a place's land, climate, ruler
    "map-culture", "map-population",  # its culture and who lives there (#4412)
    "world-keep", "world-not", "world-undo",  # World notes (#4285)
    "backstory-keep-show",  # "Let players see it" on the website's waiting card (#4432)
    "maps-add",  # a map sent from the website (#4347, slice B)
    "part-add", "part-fix", "part-state", "part-show", "part-remove",  # a thread's parts (#4476)
    "branch-move", "branch-not", "part-undo", "part-date",
    "lang-add", "lang-remove", "lang-check", "lang-undo",  # a character's languages (#4271)
    "thread-keep", "thread-not", "thread-undo",  # story threads DMbot found (#4457)
    "secret-keep", "secret-make", "secret-undo",  # 🤫 Known or secret? questions (#4598)
    "progress-keep", "progress-none", "progress-undo",  # Progress to check (#4616)
    "clue-add", "clue-word", "clue-off", "clue-undo",  # a story thread's clues (#4625)
    "claim-some",  # keeps a waiting claim for the ticked characters only (#4680)
    "backstory-keep-some",  # keeps a waiting backstory connection for the ticked (#4680)
    "lore-pdf-estimate",  # the bot counts a staged lore PDF before the yes (#4762)
    "join-everyone",  # the DM's join question, answered from the page (#4778)
    "join-party",
    "join-undo",
    "party-leave",  # who is in the party, from the page (#4778)
    "party-back",
    "party-undo",
    "party-catch-up-choices",  # who is missing what on a card (#4793)
    "party-catch-up",
    "rules-update-pick",  # a followed set's changes, picked from the page (#4841)
    "rules-update-undo",
    "heard-pick",  # "Did they mean…?" from the page (#4841)
    "heard-keep",
    "heard-type",  # "Type it…" from the page (#4841)
    "rules-update-undo-last",  # the Undo of an update DMbot took (#4841)
    "pack-update-undo-last",  # the same for a followed pack (#4841)
    "pack-update-pick",  # Pick changes on a followed pack, and its Undo (#4841)
    "pack-update-undo",
    "join-choices",  # "Let me choose" for the join question (#4799)
    "join-choose",
    "leave-yes",  # "Still in the party?" for a death or a goodbye (#4895)
    "leave-no",
    "thread-state",  # a plot thread's state from the Plot threads panel (#3696 slice 2a)
    "thread-check",
    "thread-involve",  # "Add who's involved" (#3696 slice 2b)
    "world-bias",  # ✏️ Change on a feeling DMbot read (#4932)
    "route-length", "route-length-keep", "route-undo",  # a route's stated length (#4392)
    "road-condition", "road-condition-keep", "road-condition-show",  # a road's condition (#4451)
    "thread-new", "thread-due", "thread-party",  # a new thread or promise, due note, parties (2c)
    "thread-link", "thread-led-to",  # an event on a thread, what led to what (2d)
    "reveals-ask", "reveals-keep", "reveals-not",  # what the characters learned (#3863)
    "deed-record", "deed-not", "deed-undo",  # deeds the party did (#3591)
    "continuity-ask", "continuity-answer",  # the continuity heads-ups (#3695 half 1)
)  # fmt: skip


class TheDoorKeepsEveryAction(MemoryTest):
    async def test_the_table_and_the_door_know_every_action(self) -> None:
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint"
                " WHERE conname = 'name_actions_action_check'"
                " AND conrelid = 'name_actions'::regclass"
            )
            row = await cur.fetchone()
            assert row is not None
            allowed = set(re.findall(r"'([a-z-]+)'", row["d"]))
            cur = await conn.execute(
                "SELECT string_agg(pg_get_functiondef(p.oid), E'\\n') AS d FROM pg_proc p"
                " WHERE p.oid = ANY (ARRAY(SELECT oid FROM pg_proc q WHERE q.proname ="
                " 'dmbot_web_name_action_add' AND q.pronamespace = current_schema()::regnamespace))"
            )
            door = (await cur.fetchone() or {"d": ""})["d"] or ""
        self.assertEqual(allowed, set(EVERY_ACTION))
        for action in EVERY_ACTION:
            self.assertIn(f"'{action}'", door, action)

    async def test_the_door_still_has_its_pinned_search_path(self) -> None:
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT proconfig FROM pg_proc WHERE proname = 'dmbot_web_name_action_add'"
                " AND pronamespace = current_schema()::regnamespace"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertTrue(any(c.startswith("search_path=") for c in row["proconfig"] or []))
