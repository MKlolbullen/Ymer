import io

import pytest

from app import cli


def test_prints_counts_and_top_words(tmp_path, capsys):
    path = tmp_path / "notes.txt"
    path.write_text("The cat and the dog.\nThe end\n", encoding="utf-8")
    assert cli.main([str(path), "--top", "2"]) == 0
    out = capsys.readouterr().out
    assert out == (
        "words: 7\n"
        "lines: 2\n"
        "characters: 29\n"
        "top words:\n"
        "the 3\n"
        "and 1\n"
    )


def test_default_top_is_five(tmp_path, capsys):
    path = tmp_path / "words.txt"
    path.write_text("a b c d e f g\n", encoding="utf-8")
    assert cli.main([str(path)]) == 0
    out = capsys.readouterr().out
    entries = out.split("top words:\n")[1].splitlines()
    assert entries == ["a 1", "b 1", "c 1", "d 1", "e 1"]


def test_reads_stdin_for_dash(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("Hi hi\n"))
    assert cli.main(["-"]) == 0
    out = capsys.readouterr().out
    assert "words: 2\n" in out
    assert "hi 2\n" in out


def test_missing_file_exits_1(tmp_path, capsys):
    assert cli.main([str(tmp_path / "nope.txt")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot read" in captured.err


def test_non_utf8_file_exits_1(tmp_path, capsys):
    path = tmp_path / "latin1.txt"
    path.write_bytes(b"caf\xe9\n")
    assert cli.main([str(path)]) == 1
    assert "cannot read" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_invalid_top_exits_2(tmp_path, value, capsys):
    path = tmp_path / "t.txt"
    path.write_text("x\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        cli.main([str(path), "--top", value])
    assert excinfo.value.code == 2
    assert "--top" in capsys.readouterr().err
