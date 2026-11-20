"""Partial reveals as non-specific markers for the recap (#2997; docs/STORY_MEMORY.md §9b items 6
and 7; Supervisor's ruling on #2997, the same split as MapBot slice 10b). GraphEngineer's reader:
CloudDev-lead wires it into the recap writer and its checks, behind a switch, scored before and
after.

The owner's rule, word for word: "A reveal to one or more player characters that remains hidden
information to one or more other player characters must be cited in the recap for story continuity
but the recap text must be non-specific so that spoilers are not accidentally revealed." The owner's
example of the shape: "Okthandom learned something about his mysterious friend."

Rules kept here:
- **Never the content.** A marker holds who learned, how (a private scene, a told secret, a
  check) and at most one subject, all named as the table knows them (`gate.names_for` with the
  table's viewer); never the statement, its words, or a name the table doesn't know. The rows read
  hold no words either (`memory_reveal_proposals` keeps ids only).
- **Partial only, and still true.** A reveal every player character at the session now knows is
  the table's, not a partial reveal: no marker. Nor is one the learner no longer knows (the DM
  undid it, or took the knowledge away): a kept row alone is never trusted (the reviewer's).
- **A subject only where naming it spoils nothing** (the ux-critic's): never for a check about
  another player character (it would tell that player their secret is in play), and never for an
  other name (who someone really is). The subject is the table's name for it, or none.
- **Which ones matter** (#2997 item 3):
  - a private scene between two player characters (#2996 rule 5): always;
  - a secret one character told another: always (#2997 item 3 lists it as one that matters);
  - a check's discovery: only when it touches an open thread or an entry that matters to the story
    (`mattering`, the wiring's from PlotBot), so a ranger's herb search is never cited.
- **Consent.** A row is read only when every one of its speakers is recorded now. A kept reveal
  whose speakers were forgotten keeps none and is still read: once kept, it is the DM's knowledge.
- **No cap.** Every marker that matters is cited (lengths are targets in the writer's instructions,
  never checks); one sentence per person ("Okthandom learned something about Vane and the Tower"),
  never the same sentence twice.
Pure apart from `session_markers`, which reads (the campaign's knowledge twice: once here, once in
`gate.names_for`; both in one snapshot, so they agree).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dmbot.memory import gate
from dmbot.memory.private_scene_store import _merged, _root

if TYPE_CHECKING:
    from dmbot.memory._changes import Scope

SCENE, TOLD, CHECK = "scene", "told", "check"


@dataclass(frozen=True, slots=True)
class Reveal:
    """One kept "who learned what" of the session, ids only."""

    learned_by: str  # the player character's entry id
    statement: tuple[str, str]  # (kind, id), as the gate keys it
    how: str  # TOLD or CHECK


@dataclass(frozen=True, slots=True)
class Marker:
    kind: str  # SCENE, or TOLD / CHECK (the first way this person learned something)
    who: tuple[str, ...]  # the names, as the table knows them, in order
    about: tuple[str, ...] = ()  # the subjects' names as the table knows them, in order


def _subjects(statement: tuple[str, str], data: gate.Statements) -> tuple[str, ...]:
    kind, sid = statement
    if kind == gate.ENTRY:
        return (sid,)
    if kind == gate.PROPERTY:
        owner = data.states.get(sid)
        return (owner,) if owner else ()
    if kind == gate.CONNECTION:
        return tuple(data.connections.get(sid, ()))
    return tuple(data.aliases.get(sid, ()))


def markers(
    reveals: Sequence[Reveal],
    scenes: Sequence[tuple[str, str]],
    data: gate.Statements,
    at_session: Collection[str],
    names: Mapping[str, str | None],
    mattering: Collection[str],
) -> list[Marker]:
    """The markers the recap must cite, in order (scenes first, then each person who learned
    something, in the order they first did). `scenes`: each kept private scene's two player
    characters. `names`: entry id to its name as the table knows it (None: the table can't name
    it). `mattering`: the entries that matter to the story, open threads' included."""
    table = gate.visible(gate.Viewer.the_table(at_session), data)
    out: list[Marker] = []
    pairs: set[tuple[str, ...]] = set()
    for first, second in scenes:
        pair = tuple(sorted((first, second)))
        a, b = names.get(first), names.get(second)
        if pair in pairs or first not in at_session or second not in at_session or not a or not b:
            continue  # (one not at the table, or one the table can't name, can't be cited)
        pairs.add(pair)
        out.append(Marker(SCENE, (a, b)))
    learned: dict[str, tuple[str, list[str]]] = {}  # name -> (first way, subjects)
    for how in (TOLD, CHECK):
        for r in reveals:
            if r.how != how or r.learned_by not in at_session or r.statement in table.sureness:
                continue  # (not at the table, or now the table's: not a partial reveal)
            if r.statement not in gate.visible(gate.Viewer.character(r.learned_by), data).sureness:
                continue  # (undone, or taken away since: never cited from the kept row alone)
            subjects = [s for s in _subjects(r.statement, data) if s != r.learned_by]
            if how == CHECK and not any(s in mattering for s in subjects):
                continue  # (a trivial discovery is never cited)
            who = names.get(r.learned_by)
            if not who:
                continue
            nameable = [
                s for s in subjects
                if names.get(s)
                and r.statement[0] != gate.ALIAS  # (who someone really is: never a subject)
                and not (how == CHECK and s in data.player_characters)
            ]  # fmt: skip
            _way, about = learned.setdefault(who, (how, []))
            if nameable and (name := names[nameable[0]]) and name not in about:
                about.append(name)
    out.extend(Marker(way, (who,), tuple(about)) for who, (way, about) in learned.items())
    return out


def _and(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def words(marker: Marker) -> str:
    """'Okthandom learned something about Vane.' Never what: the recap cites it, no more."""
    who = _and(marker.who)
    if marker.kind == SCENE:
        return f"{who} had a private word."
    about = f" about {_and(marker.about)}" if marker.about else ""
    return f"{who} learned something{about}."


async def session_markers(
    scope: Scope,
    session_started_at: int,
    at_session: Collection[str],
    mattering: Collection[str],
    opted_in: Collection[int],
) -> list[Marker]:
    """This session's markers, read from the kept reveals (`memory_reveal_proposals`, status kept)
    and kept private scenes (`memory_private_scenes`), whose speakers are all in `opted_in`.
    `at_session`: the player characters at the session (the table)."""
    if not at_session:
        return []
    recorded = sorted(set(opted_in))
    cur = await scope.conn.execute(
        "SELECT character_id, statement_kind, statement_id, how FROM memory_reveal_proposals"
        " WHERE guild_id = %s AND campaign_id = %s AND status = 'kept'"
        " AND session_started_at = %s AND speaker_ids <@ %s::bigint[] ORDER BY created_at, id",
        (scope.guild_id, scope.campaign_id, session_started_at, recorded),
    )
    reveals = [
        Reveal(
            str(r["character_id"]),
            (str(r["statement_kind"]), str(r["statement_id"])),
            TOLD if r["how"] == "tell" else CHECK,
        )
        for r in await cur.fetchall()
    ]
    cur = await scope.conn.execute(
        "SELECT first_id, second_id FROM memory_private_scenes WHERE guild_id = %s"
        " AND campaign_id = %s AND status = 'kept' AND session_started_at = %s"
        " AND speaker_ids <@ %s::bigint[] ORDER BY from_ms, id",
        (scope.guild_id, scope.campaign_id, session_started_at, recorded),
    )
    scenes = [(str(r["first_id"]), str(r["second_id"])) for r in await cur.fetchall()]
    if not reveals and not scenes:
        return []
    # (a character merged since is cited under the entry they became, as the scenes list does)
    merged = await _merged(scope.conn, scope.guild_id, scope.campaign_id)
    scenes = [(_root(merged, a), _root(merged, b)) for a, b in scenes]
    reveals = [Reveal(_root(merged, r.learned_by), r.statement, r.how) for r in reveals]
    data = await gate.load(scope)
    wanted = {r.learned_by for r in reveals} | {i for pair in scenes for i in pair}
    wanted |= {s for r in reveals for s in _subjects(r.statement, data)}
    names = await gate.names_for(scope, gate.Viewer.the_table(at_session), sorted(wanted))
    return markers(reveals, scenes, data, at_session, names, mattering)
