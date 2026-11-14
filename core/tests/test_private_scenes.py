"""Private scenes between two player characters (#2996 rule 5): found by code from the game clock
and who spoke, never by the AI. Pure; invented names."""

from __future__ import annotations

import unittest

from dmbot.memory.private_scenes import Clock, Scene, find

WREN, ASH, BRAM = "w" * 32, "a" * 32, "b" * 32
DM, P1, P2, P3 = 1, 7, 8, 9
PLAYERS = {WREN: P1, ASH: P2, BRAM: P3}


def clock(split_at: int | None = 600, *, groups: dict[str, int] | None = None) -> Clock:
    """Together from 100; with `split_at`, split from then into `groups` (default: Wren and Ash in
    1, Bram in 2)."""
    main = [(100, False)] + ([(split_at, True)] if split_at is not None else [])
    placed = groups if groups is not None else {WREN: 1, ASH: 1, BRAM: 2}
    rows = {c: [(split_at, g)] for c, g in placed.items()} if split_at is not None else {}
    return Clock(main, rows)


def at(seconds: int, speaker: int) -> tuple[int, int]:
    return (seconds * 1000, speaker)


class Finding(unittest.TestCase):
    def test_two_characters_alone_both_speaking_is_a_private_scene(self) -> None:
        lines = [at(650, P1), at(660, DM), at(670, P2), at(680, P1)]
        (scene,) = find(lines, clock(), PLAYERS)
        # (the DM's line can't be placed in a group: counted in no scene)
        self.assertEqual(scene, Scene(ASH, WREN, 650_000, 680_000, 3, (P1, P2)))

    def test_only_one_of_the_two_speaking_is_no_scene(self) -> None:
        self.assertEqual(find([at(650, P1), at(660, DM), at(670, P1)], clock(), PLAYERS), [])

    def test_the_party_together_is_no_scene(self) -> None:
        self.assertEqual(find([at(650, P1), at(670, P2)], clock(None), PLAYERS), [])

    def test_lines_before_the_split_are_not_in_it(self) -> None:
        (scene,) = find([at(550, P1), at(650, P1), at(670, P2)], clock(), PLAYERS)
        self.assertEqual((scene.from_ms, scene.line_count), (650_000, 2))

    def test_three_characters_in_the_group_is_no_scene(self) -> None:
        groups = {WREN: 1, ASH: 1, BRAM: 1}
        self.assertEqual(find([at(650, P1), at(670, P2)], clock(groups=groups), PLAYERS), [])

    def test_one_character_with_npcs_is_no_scene(self) -> None:
        groups = {WREN: 1, ASH: 2, BRAM: 2}
        self.assertEqual(find([at(650, P1), at(660, DM)], clock(groups=groups), PLAYERS), [])

    def test_one_player_with_two_characters_is_no_scene(self) -> None:
        players = {WREN: P1, ASH: P1, BRAM: P3}  # (Supervisor: two different people)
        self.assertEqual(find([at(650, P1), at(670, P1)], clock(), players), [])

    def test_the_other_group_talking_neither_ends_it_nor_joins_it(self) -> None:
        lines = [at(650, P1), at(655, P3), at(660, P2)]  # (the DM moves between the groups)
        (scene,) = find(lines, clock(), PLAYERS)
        self.assertEqual((scene.line_count, scene.speaker_ids), (2, (P1, P2)))

    def test_two_pairs_can_each_have_a_scene_at_once(self) -> None:
        cara, P4 = "c" * 32, 10
        players = {**PLAYERS, cara: P4}
        groups = {WREN: 1, ASH: 1, BRAM: 2, cara: 2}
        lines = [at(650, P1), at(651, P3), at(652, P2), at(653, P4)]
        scenes = find(lines, clock(groups=groups), players)
        self.assertEqual([(s.first_id, s.second_id) for s in scenes], [(ASH, WREN), (BRAM, cara)])

    def test_the_clock_rejoining_ends_the_scene(self) -> None:
        main = [(100, False), (600, True), (700, False)]
        placed = {WREN: [(600, 1)], ASH: [(600, 1)], BRAM: [(600, 2)]}
        lines = [at(650, P1), at(660, P2), at(710, P1), at(720, P2)]
        (scene,) = find(lines, Clock(main, placed), PLAYERS)
        self.assertEqual((scene.to_ms, scene.line_count), (660_000, 2))

    def test_a_rejoin_and_a_new_split_with_nobody_speaking_between_is_two_scenes(self) -> None:
        main = [(100, False), (600, True), (700, False), (800, True)]
        placed = {WREN: [(600, 1), (800, 1)], ASH: [(600, 1), (800, 1)], BRAM: [(600, 2), (800, 2)]}
        lines = [at(650, P1), at(660, P2), at(850, P1), at(860, P2)]
        scenes = find(lines, Clock(main, placed), PLAYERS)
        self.assertEqual([(s.from_ms, s.to_ms) for s in scenes],
                         [(650_000, 660_000), (850_000, 860_000)])  # fmt: skip

    def test_the_group_changing_ends_it(self) -> None:
        placed = {WREN: [(600, 1), (700, 2)], ASH: [(600, 1)], BRAM: [(600, 2)]}
        lines = [at(650, P1), at(660, P2), at(710, DM), at(720, P2)]
        (scene,) = find(lines, Clock([(100, False), (600, True)], placed), PLAYERS)
        self.assertEqual(scene.line_count, 2)

    def test_no_clock_at_all_is_together(self) -> None:
        self.assertEqual(find([at(650, P1), at(670, P2)], Clock([], {}), PLAYERS), [])
