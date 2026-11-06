"""The Story Graph's words for a `knows` connection that carries a feeling (#3180, #3175).

Pure (no database): the website's edges are worded by the same `link_words` as Discord's cards, so a
`knows` with one of the nine feeling words reads "knows (hostile)" and the old friend and enemy
connections read as they always did."""

from __future__ import annotations

from typing import Any

import pytest

from dmbot.memory import reputation_words
from dmbot.web.relationships import edges_from


def _words(predicate: str, detail: str) -> str:
    row: dict[str, Any] = {
        "id": "c1",
        "a": "x",
        "b": "y",
        "predicate": predicate,
        "detail": detail,
        "secret": False,
        "at": 1,
    }
    edge = edges_from([row], can_edit=False)[0]
    return str(edge["links"][0]["words"])


@pytest.mark.parametrize("word", reputation_words.WORDS)
def test_each_feeling_word_shows_in_brackets(word: str) -> None:
    assert _words("knows", word) == f"knows ({word})"


def test_a_word_off_the_list_is_plain_knows() -> None:
    assert _words("knows", "adores") == "knows"
    assert _words("knows", "") == "knows"


def test_the_old_friend_and_enemy_read_as_before() -> None:
    assert _words("ally_of", "") == "is a friend or ally of"
    assert _words("enemy_of", "") == "is an enemy of"
