"""Two voices recorded alone, one file each (#534): the DM's lines in one, the player's in
the other, each reader staying quiet while the other's lines would be read. The twin cuts
each file into turns at those long quiet stretches, then lays the turns out in the
script's order, each said by its own made-up speaker, so a table of two can be replayed
without a table.

How the turns follow each other is the twin's choice, not the readers': each turn starts
`answer_ms` after the one before it ends (negative: the next speaker talks over the end).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from dmbot.devtools.replay.audio import FRAME_MS, SPEECH_END_MS, TWIN_PLAYER, TWIN_SPEAKER, Piece
from dmbot.transcription.base import MIN_UTTERANCE_S

# Quiet this long inside one voice's file means the other voice speaks here. Longer than
# a dramatic pause (a count to three), shorter than the count to ten the script asks for.
TURN_QUIET_MS = 5000
ANSWER_MS = 800  # live, the next person starts talking about this soon
SPEAKERS = {"DM": TWIN_SPEAKER, "Player": TWIN_PLAYER}
ROLES = {speaker: role for role, speaker in SPEAKERS.items()}


def _short(piece: Piece) -> bool:
    """Too short to be written down: a tap, a bump, a breath."""
    return len(piece.frames) * FRAME_MS < MIN_UTTERANCE_S * 1000


def _grouped(pieces: Sequence[Piece], quiet_ms: int) -> list[list[Piece]]:
    out: list[list[Piece]] = []
    for piece in pieces:
        if out and piece.start_ms - out[-1][-1].end_ms < quiet_ms:
            out[-1].append(piece)
        else:
            out.append([piece])
    return out


def turns(pieces: Sequence[Piece], quiet_ms: int = TURN_QUIET_MS) -> list[list[Piece]]:
    """Pieces of one voice, grouped into turns at `quiet_ms` of quiet or more. A short
    sound inside a turn stays in it and plays; one on its own (a bump before the opening
    quiet) is no turn, and is left out: it has no place in the other voice's timeline."""
    return [g for g in _grouped(pieces, quiet_ms) if not all(map(_short, g))]


def lone_sounds(pieces: Sequence[Piece], quiet_ms: int = TURN_QUIET_MS) -> int:
    """How many short sounds on their own `turns` leaves out."""
    return sum(len(g) for g in _grouped(pieces, quiet_ms) if all(map(_short, g)))


def mix(
    order: Sequence[str],
    voices: Mapping[str, Sequence[Piece]],
    names: Mapping[str, str],
    *,
    turn_quiet_ms: int = TURN_QUIET_MS,
    answer_ms: int = ANSWER_MS,
    speech_end_ms: int = SPEECH_END_MS,
) -> list[Piece]:
    """The voices' turns in the script's `order` ("DM", "Player", ...), each piece moved
    to its place and given its role's speaker. `names`: each role's file, for errors.
    `speech_end_ms`: the quiet that ends a piece, kept between one role's own turns so
    they never join. Raises ValueError if a file's turns don't match the script's."""
    grouped = {role: turns(voices.get(role, ()), turn_quiet_ms) for role in SPEAKERS}
    for role, found in grouped.items():
        expected = order.count(role)
        if len(found) != expected:
            raise ValueError(
                f"{names.get(role, role)}: {len(found)} turns found, but the script has "
                f"{expected} [{role}] turns. A turn ends at {turn_quiet_ms / 1000:g} s of "
                "quiet, and sounds under 0.25 s on their own don't count: was the count to "
                "ten at each of the other voice's lines kept? (--turn-quiet-ms changes it)"
            )
    out: list[Piece] = []
    taken = dict.fromkeys(SPEAKERS, 0)
    ended: dict[str, int] = {}  # where each role's last turn ended
    cursor = 0
    for role in order:
        group = grouped[role][taken[role]]
        taken[role] += 1
        # Talking over someone else, never over oneself: a role's turns stay apart.
        start = max(cursor, ended.get(role, -speech_end_ms) + speech_end_ms)
        shift = start - group[0].start_ms
        out.extend(piece.moved(shift, SPEAKERS[role]) for piece in group)
        ended[role] = group[-1].end_ms + shift
        cursor = max(start, ended[role] + answer_ms)
    return out
