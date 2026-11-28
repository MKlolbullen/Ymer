from app.counter import TextStats, analyze


def test_empty_text():
    stats = analyze("")
    assert (stats.words, stats.lines, stats.characters) == (0, 0, 0)


def test_basic_counts():
    stats = analyze("hello world\nfoo  bar\tbaz\n")
    assert isinstance(stats, TextStats)
    assert stats.words == 5
    assert stats.lines == 2
    assert stats.characters == 25


def test_no_trailing_newline():
    stats = analyze("one line")
    assert stats.lines == 0
    assert stats.words == 2


def test_unicode_characters_counted_not_bytes():
    stats = analyze("café\n")
    assert stats.characters == 5
