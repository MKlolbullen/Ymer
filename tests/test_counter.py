"""Unit tests for counter.analyze, covering the counts and the top words."""

from app.counter import TextStats, analyze


def test_empty_text_gives_all_zeros_and_no_top_words():
    assert analyze("") == TextStats(words=0, lines=0, characters=0, top_words=[])


def test_whitespace_only_text():
    stats = analyze("  \n\t\n")
    assert (stats.words, stats.lines, stats.characters) == (0, 2, 5)
    assert stats.top_words == []


def test_text_without_trailing_newline():
    stats = analyze("first line\nsecond line")
    assert stats.words == 4
    assert stats.lines == 1  # only newline characters are counted
    assert stats.characters == 22


def test_multi_line_text_with_blank_line():
    text = "alpha beta\n\ngamma\ndelta epsilon zeta\n"
    stats = analyze(text)
    assert stats.words == 6
    assert stats.lines == 4
    assert stats.characters == len(text) == 37


def test_case_insensitive_merging():
    stats = analyze("The the THE tHe cat")
    assert stats.top_words == [("the", 4), ("cat", 1)]


def test_ties_ordered_by_count_then_alphabetically():
    stats = analyze("zebra mango apple mango zebra kiwi", top=4)
    assert stats.top_words == [
        ("mango", 2),
        ("zebra", 2),
        ("apple", 1),
        ("kiwi", 1),
    ]


def test_tie_cut_off_at_n_keeps_alphabetical_first():
    stats = analyze("c b a", top=2)
    assert stats.top_words == [("a", 1), ("b", 1)]


def test_top_larger_than_distinct_words_returns_all():
    stats = analyze("one two Two three three THREE", top=50)
    assert stats.top_words == [("three", 3), ("two", 2), ("one", 1)]


def test_default_top_is_five():
    stats = analyze("a b c d e f g h")
    assert stats.top_words == [("a", 1), ("b", 1), ("c", 1), ("d", 1), ("e", 1)]
