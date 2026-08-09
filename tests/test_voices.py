from app.video.voices import words_from_alignment


def test_alignment_groups_into_words():
    a = {
        "characters": list("hi there"),
        "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
        "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
    }
    w = words_from_alignment(a)
    assert [x["word"] for x in w] == ["hi", "there"]
    assert w[0]["start"] == 0.0 and w[1]["end"] == 0.8


def test_empty_alignment_is_safe():
    assert words_from_alignment({}) == []
