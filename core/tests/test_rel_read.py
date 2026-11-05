"""The relationship reader's instructions and the check of its answer (#3244 slice 4b)."""

from collections import Counter

from dmbot.memory import rel_read, reputation_words

KNOWN = ["Tavin", "Quill", "Vela"]


def _parse(answer: str, counts: Counter[str] | None = None) -> list[rel_read.Tie]:
    return rel_read.parse(answer, {1, 2, 3}, KNOWN, counts)


def test_one_sentence_two_ties_make_two_rows() -> None:
    ties = _parse(
        "1 | Quill | Tavin | kin_of | - | Quill is Tavin's uncle.\n"
        "1, 2 | quill | TAVIN | mentors | - | Quill taught Tavin swordplay.\n"
    )
    assert [(t.a, t.b, t.kind, t.word) for t in ties] == [
        ("Quill", "Tavin", "kin_of", None),
        ("Quill", "Tavin", "mentors", None),
    ]
    assert ties[1].sources == (1, 2)


def test_knows_keeps_one_of_the_nine_words_and_maps_a_stance() -> None:
    ties = _parse(
        "1 | Tavin | Vela | knows | friendly | They trust each other's company.\n"
        "2 | Tavin | Quill | knows | at war | Their houses are at war.\n"
        "3 | Vela | Quill | knows | smitten | Vela likes Quill.\n"
    )
    assert [t.word for t in ties] == [
        reputation_words.FRIENDLY,
        reputation_words.HOSTILE,
        None,
    ]


def test_a_word_is_dropped_for_any_other_type() -> None:
    (tie,) = _parse("1 | Quill | Tavin | mentors | friendly | Quill taught Tavin.")
    assert tie.word is None


def test_bad_rows_are_dropped_and_counted() -> None:
    counts: Counter[str] = Counter()
    ties = _parse(
        "not a row\n"
        "9 | Quill | Tavin | kin_of | - | Line nine is not here.\n"
        "1 | Quill | The Grey Fox | kin_of | - | An epithet is not on the list.\n"
        "1 | Quill | Quill | kin_of | - | Same person twice.\n"
        "1 | Quill | Tavin | kin_of | - |   \n"
        "1 | Quill | Tavin | cousin_of | - | An unlisted type stays, as other.\n",
        counts,
    )
    assert [(t.kind) for t in ties] == [rel_read.UNSURE]
    assert dict(counts) == {
        "unreadable": 1,
        "empty": 1,
        "bad_source": 1,
        "unknown_name": 1,
        "same_name": 1,
        "unlisted_type": 1,
    }


def test_a_long_sentence_is_kept_whole() -> None:
    (tie,) = _parse("1 | Quill | Tavin | kin_of | - | " + "word " * 400)
    assert len(tie.sentence) > 1500


def test_instructions_list_names_types_and_words_only() -> None:
    text = rel_read.instructions(["Tavin", "Vela"])
    assert "- Tavin" in text and "- Vela" in text and "Quill" not in text.split("Names:")[1]
    assert "kin_of" in text and "term_of_address" in text
    assert all(w in text for w in reputation_words.WORDS)


def test_a_long_feeling_or_a_capitalised_type_keeps_the_tie() -> None:
    counts: Counter[str] = Counter()
    ties = rel_read.parse(
        "1 | Tavin | Vela | Knows | they are quite wary of each other, honestly | Wary of her.\n"
        "2 | Grey  Fox | Vela | Kin_Of | - | Cousins.\n"
        "3 | Tavin | Vela | mentors | - |",
        {1, 2, 3},
        ["Tavin", "Vela", "Grey Fox"],
        counts,
    )
    assert [(t.kind, t.word) for t in ties] == [("knows", None), ("kin_of", None)]
    assert dict(counts) == {"unset_word": 1, "empty": 1}


def test_counts_default_and_empty_sources() -> None:
    assert rel_read.parse("1 | Tavin | Vela | knows | friendly | x", set(), ["Tavin", "Vela"]) == []


def test_the_instructions_tie_a_hired_hand_to_the_person_not_the_group() -> None:
    text = rel_read.instructions(["Vela", "Hale"])
    assert "bodyguard employed by a merchant" in text
    assert "not to the group" in text
    assert "hires others against a third person" in text
