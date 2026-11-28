from app.counter import analyze, normalize_words, top_words


def test_case_insensitive_and_punctuation_stripped():
    text = "The cat. the CAT, \"the\" dog!"
    assert top_words(text, 5) == [("the", 3), ("cat", 2), ("dog", 1)]


def test_drops_tokens_that_are_only_punctuation():
    assert normalize_words("hello -- world ... !") == ["hello", "world"]


def test_inner_punctuation_kept():
    assert normalize_words("Don't e-mail") == ["don't", "e-mail"]


def test_tie_break_alphabetical():
    text = "pear apple banana apple pear cherry"
    assert top_words(text, 3) == [("apple", 2), ("pear", 2), ("banana", 1)]


def test_fewer_distinct_words_than_n():
    assert top_words("one two two", 10) == [("two", 2), ("one", 1)]


def test_empty_text_and_zero_top():
    assert top_words("", 5) == []
    assert top_words("a b c", 0) == []


def test_analyze_default_top_is_five():
    text = "a b c d e f g a"
    stats = analyze(text)
    assert len(stats.top_words) == 5
    assert stats.top_words[0] == ("a", 2)
    assert analyze(text, top=2).top_words == [("a", 2), ("b", 1)]
