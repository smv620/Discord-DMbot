"""Who reads `memory_relations` (#2412, slice 2b). A reading is placed on a *proposed* secret
connection when nobody has kept it (`placing.py`), so every reader of connections must either
see only confirmed ones or be a DM-side reader on purpose. This list is reviewed by hand: a new
file that touches the table fails here until someone decides which kind it is and writes why, the
way `test_campaign_move_cascade` makes a new table choose."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from tests.pg import DatabaseTest

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

# file → how it treats a proposed connection
CONFIRMED_ONLY = "reads confirmed connections only (and, for players, only those the party holds)"
DM_ONLY = "DM-side by design"
READERS = {
    "lineage.py": "only names the table, on the list that carries the build's stamp (#3739)",
    "memory/recall.py": CONFIRMED_ONLY + ": the walk behind NPC pages, table answers and recaps",
    "memory/importance.py": CONFIRMED_ONLY,
    "memory/importance_rank.py": CONFIRMED_ONLY
    + ": counts current, confirmed connections for a rank floor; never shown to a player",
    "memory/importance_step.py": CONFIRMED_ONLY
    + ": the connections the importance reading is shown, DM-only; never for a player",
    "memory/reveal_proposals.py": CONFIRMED_ONLY
    + ": who learned what is placed only on a confirmed connection, for the DM (#2996)",
    "memory/role_store.py": CONFIRMED_ONLY
    + ": the roles current, confirmed connections show, for the DM's why; never for a player",
    "memory/continuity.py": CONFIRMED_ONLY + ": the counts and the story's world",
    "memory/backstory_links.py": CONFIRMED_ONLY + ": pairs already known",
    "memory/hand_over.py": DM_ONLY + ": current confirmed owns and carries of a one-of-a-kind "
    "item, for the DM's private question (#4115); ends a tie only on the DM's answer",
    "memory/worked_out.py": "proposes links; players see only confirmed ones, and a link from a "
    "secret, unrevealed or hidden source starts secret",
    "memory/exposures.py": DM_ONLY + ": secret connections said aloud (confirmed ones)",
    "memory/states.py": "live located_in facts, without secret proposals that only hold guesses",
    "memory/forget_readings.py": "Stop recording me removes a connection made only to hold a guess",
    "memory/beliefs.py": "beliefs hang on any connection; every players' reader filters on the "
    "party's belief at 1.0, which a guess never is",
    "memory/contradictions.py": DM_ONLY
    + ": kept connections and waiting guess connections, ids and numbers, no words",
    "memory/contradiction_grades.py": "only checks that the two connections of a grade still exist",
    "memory/contradiction_grader.py": DM_ONLY + ": reads kept and guess connections to put a clash "
    "into the campaign's words for PlotBot; ids and names, no transcript",
    "memory/difference_sorter.py": DM_ONLY
    + ": reads kept and guess connections to put a difference "
    "into the campaign's words for PlotBot; ids and names, no transcript",
    "memory/report_store.py": "only checks that the connections of a difference still exist",
    "memory/claims.py": "DM-side: reads only the facts made by kept described-person claims of "
    "one session, to find the person they made; shows players nothing (#3431)",
    "memory/party.py": DM_ONLY + ": the party fill reads member_of ties to find the group the "
    "campaign already uses for the party (#3954); shows players nothing",
    "memory/placing.py": "writes the proposed connections and their beliefs for the DM",
    "memory/gate.py": "the gate (#2995): reads each connection's ends to say which entries a "
    "viewer learns, and only for connections its list says that viewer knows",
    "memory/kind_offers.py": DM_ONLY
    + ": counts live, confirmed catch-all connections by their words; no player sees it",
    "memory/knowers.py": DM_ONLY + ": who knows a connection; only checks it exists, any status",
    "memory/as_of.py": "what a point of view believed at a game time: the DM any view, live "
    "connections; a player only their own characters', on confirmed connections the gate lets "
    "them know",
    "memory/lies.py": "who thinks someone lied: the DM sees every live connection's; a player only "
    "their own characters', on confirmed connections the gate lets them know",
    "memory/lineage.py": DM_ONLY
    + ": which beliefs rest on an entry (ids and numbers, no words); live connections, proposed or"
    " confirmed, because the DM chooses what to change",
    "memory/notify.py": DM_ONLY + ": what the DM is told",
    "memory/graph_review_run.py": DM_ONLY
    + ": the DM's own fixed connections and their lore quotes, read for the careful check (#3741); "
    "the report is DM-only and never shown to players",
    "memory/health.py": DM_ONLY + ": counts for the DM, not shown to players",
    "memory/set_definitions.py": "is a relationship word in use: any status, on purpose",
    "memory/backup.py": "a backup is complete: DM secrets and proposals travel as they are",
    "memory/_changes.py": "the change log and the row writers",
    "campaigns/tables.py": "the list of tables a campaign owns",
    "devtools/sample_run/counts.py": CONFIRMED_ONLY
    + ": dev1's sample report (#3545); counts confirmed secret connections by source, prints "
    "numbers only (no name, id or word), nothing reaches a player",
    "devtools/movers_check.py": CONFIRMED_ONLY
    + ": dev1's read-only command (#3363); one SELECT of confirmed, current place ties, prints "
    "counts only (no name, id or word), nothing reaches a player",
}


# memory/store.py, by function: it holds most of the readers, so one entry for the file would let a
# new query in it pass unseen. Every function that names the table is here with how it treats a
# guess connection (proposed, secret, the extractor's).
STORE = {
    "_ai_join_refusal": DM_ONLY + ": a secret, held-back or disguise link makes it say no",
    "_close_stale_flags": DM_ONLY + ": flags",
    "_sync_plays": "plays links only (scan proposals, never secret guesses)",
    "_threads": CONFIRMED_ONLY,
    "_standing_index": CONFIRMED_ONLY,
    "_deed_rows": CONFIRMED_ONLY,
    "make_kind_of_words": CONFIRMED_ONLY
    + ": moves the confirmed catch-all connections of the DM's words",
    "_covered_words": CONFIRMED_ONLY
    + ": the words of live catch-all connections, for the DM's offer",
    "_audience": CONFIRMED_ONLY + ": who may be told",
    "_live_led_to": CONFIRMED_ONLY + ': only live, confirmed "led to" links, for the loop check',
    "_recheck_led_to_after_undo": CONFIRMED_ONLY
    + ': re-checks a "led to" link an Undo made live again',
    "_relations_touching": DM_ONLY + ": the checks, which skip guess connections themselves",
    "flag_kept": DM_ONLY + ": checks a connection the DM keeps",
    "_delete_relation": DM_ONLY + ": removal with its flags",
    "_move_relations": DM_ONLY + ": a merge; carries beliefs to the twin",
    "_met_back": "writes: moves the other's own knows up to met (#3881); never shows anything",
    "decline_setting": DM_ONLY + ": a made place for a declined setting guess goes only if nothing "
    "points at it (#4299)",
    "set_knows_how": "writes: the DM's has met / is aware of (#3881); never shows anything",
    "set_direction": DM_ONLY + ": which way a connection reads (#4154); a turn is a rewrite",
    "_replace_relation_in": DM_ONLY
    + ": new words or a turn: the connection written anew, the old one ended (#4154)",
    "_recheck_companions": DM_ONLY
    + ": a merge or a change of kind refuses a companion link left invalid",
    "_let_go_of_anchored": DM_ONLY
    + ": only counts live connections, to decide a rejection (#2790)",
    "relations": "all live ones, without guess connections (they are not connections yet)",
    "lookup_data": CONFIRMED_ONLY + " (a proposed one only while not secret)",
    "same_person_offers": CONFIRMED_ONLY
    + ": evidence for a DM-only question, never a connection shown",
    "set_condition_in": DM_ONLY + ": what an entry's condition touches",
    "set_unique": DM_ONLY + ": one-of-a-kind items",
    "chain": CONFIRMED_ONLY,
    "waiting_connections": CONFIRMED_ONLY + ": and hidden ones, for the DM",
    "add_knower_in": CONFIRMED_ONLY,
    "remove_knower": DM_ONLY,
    "analyze": DM_ONLY + ": counts for the DM",
    "reveal_exposed": CONFIRMED_ONLY,
    "check_people": "plays links only",
    "learn_people": "plays links only",
    "people_suggestions": "plays links only",
    "table_people": "plays links only",
    "decide_plays": "plays links only",
    "forget_learned": "plays links only",
    "recheck_definitions": CONFIRMED_ONLY,
    "add_relation_in": "writes: `placing` makes a guess connection here (fresh, leave, made_by)",
    "update_relation": "writes: the DM's Keep or No",
    "tag_ties": "confirmed ties for the DM's private same-person question, secret ones marked; "
    "the tag never shows a secret tie to a player",
    "group_hints": "confirmed member_of ties for the DM's name question; names only",
    "_confirm_group_memberships": "writes: confirms the DM's own proposed member_of; no reading",
    "_settle_whereabouts": "writes: ends an item's other whereabouts; shows players nothing",
    "pinned_connections": CONFIRMED_ONLY,
    "_view": CONFIRMED_ONLY + ": the party's and the DM's views",
    "relation": DM_ONLY + ": one connection by id",
    "change_relation_words": "requires a confirmed connection",
    "undo_connection": DM_ONLY + ": an Undo",
    "link_party": DM_ONLY + ": a writer's own check (every status); shows players nothing",
    "isolated_rows_in": "kept and waiting links, no guess connections, for the DM's graph "
    "clean-up (#3740); ids and words only, never shown to a player",
    "record_link_source": "writes: remembers the upload label of a kept connection; reads no tie",
    "reveal_stated_in": "writes: shows a held-back connection the table stated (#3963 c), only "
    "a confirmed, live, non-secret one whose ends are both shown",
    "learnable_in_scene": "reads one connection to decide whether the group in a split scene may "
    "learn it (#2996); writes nothing, shows players nothing",
    "live_between": "which live, confirmed connections join two entries and which are held "
    "back, so a catch-all claim beside one is held back too (#3975); shows players nothing",
}


def _store_functions() -> set[str]:
    source = (SRC / "memory" / "store.py").read_text()
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            text = ast.get_source_segment(source, node) or ""
            if re.search(r"memory_relations|\bRELATIONS\b", text):
                found.add(node.name)
    return found


class WhoReadsConnections(unittest.TestCase):
    def test_every_file_that_touches_the_table_is_on_the_reviewed_list(self) -> None:
        found = {
            str(path.relative_to(SRC))
            for path in SRC.rglob("*.py")
            if re.search(r"memory_relations|\bRELATIONS\b", path.read_text())
            and "migrations" not in path.relative_to(SRC).parts  # (the SQL files, #2127)
        } - {"memory/store.py", "schema.py"}  # (store.py by function below; schema.py by door)
        self.assertEqual(
            sorted(found),
            sorted(READERS),
            "A file reads or writes connections. If it can show one to players or count one in "
            "something they see, it must filter status = 'confirmed' (a reading's proposed "
            "connection is secret and unproved); otherwise add it here with the reason.",
        )

    def test_every_function_in_the_store_that_touches_the_table_is_reviewed(self) -> None:
        self.assertEqual(sorted(_store_functions()), sorted(STORE))


class TheDoors(DatabaseTest):
    async def test_every_door_that_reads_connections_filters_confirmed(self) -> None:
        """The doors as the database has them (the last definition of each, not a string in the
        Python that builds it): each must filter on `status = 'confirmed'`."""
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT proname, prosrc FROM pg_proc p"
                " JOIN pg_namespace n ON n.oid = p.pronamespace"
                " WHERE n.nspname = current_schema() AND prosrc ILIKE '%memory_relations%'"
                " ORDER BY proname"
            )
            doors = {r["proname"]: r["prosrc"] for r in await cur.fetchall()}
        self.assertTrue(doors)  # the pin is looking at something
        keywords = {"where", "on", "join", "left", "inner", "set", "using", "as", "cross", "for"}
        loose: list[str] = []
        for name, src in doors.items():
            for found in re.finditer(r"memory_relations(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?", src):
                alias = found.group(1)
                alias = None if alias is None or alias.lower() in keywords else alias
                pattern = (  # the filter on THIS use of the table, not on any other table
                    rf"\b{alias}\.status\s*=\s*'confirmed'"
                    if alias
                    else r"(?<![.\w])status\s*=\s*'confirmed'"
                )
                if not re.search(pattern, src):
                    loose.append(f"{name} ({alias or 'no alias'})")
        self.assertEqual(loose, [], "a door reads connections without the confirmed filter")


if __name__ == "__main__":
    unittest.main()
