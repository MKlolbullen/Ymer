"""The formula language (ymer.formulas) on its own."""

import pytest

from ymer.formulas import FormulaError, compile_formula, evaluate, references, rename_property


def run(text: str, **values):
    values = {k.replace("_", " "): v for k, v in values.items()}
    return evaluate(compile_formula(text), values.get)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("2 ^ 3 ^ 2", 512),
        ("-2 ^ 2", -4),
        ("7 % 4", 3),
        ("10 / 4", 2.5),
        ("1 / 0", None),
        ('"a" + 1', "a1"),
        ('concat("x", 2, true)', "x2true"),
        ("1 == 1 and 2 > 1", True),
        ("not (1 > 2) && !false", True),
        ("1 = 1", True),
        ('"ABC" == "abc"', True),
        ("round(2.345, 2)", 2.35),
        ("round(-2.5)", -3),
        ("floor(2.7) + ceil(2.1)", 5),
        ("min(3, 1, 2) + max(1, 5)", 6),
        ('if(true, "yes", "no")', "yes"),
        ('if(false, "yes")', None),
        ("ifs(false, 1, false, 2, 3)", 3),
        ("ifs(false, 1)", None),
        ('length("hello")', 5),
        ('upper("a") + lower("B")', "Ab"),
        ('contains("Systems Security", "security")', True),
        ('replace("a-b", "-", "+")', "a+b"),
        ('toNumber("about 42 points")', 42),
        ('dateBetween("2026-12-04", "2026-11-02", "days")', 32),
        ('dateBetween("2026-12-04", "2026-11-02", "months")', 1),
        ('dateAdd("2026-01-31", 1, "months")', "2026-02-28"),
        ('dateAdd("2026-11-02", 2, "weeks")', "2026-11-16"),
        ('year("2026-11-02") * 100 + month("2026-11-02")', 202611),
        ("null", None),
    ],
)
def test_expressions(text, expected):
    assert run(text) == expected


def test_properties_lookups_and_empty_values():
    pct = 'prop("Score") / prop("Max Points") * 100'
    assert run(pct, Score=58, Max_Points=[100]) == 58  # a lookup of one value
    assert run(pct, Score=None, Max_Points=[100]) is None
    assert run(pct, Score=5, Max_Points=[]) is None
    letter = 'if(empty(prop("G")), "", ifs(prop("G") >= 90, "A", prop("G") >= 80, "B", "F"))'
    assert run(letter, G=93.7) == "A"
    assert run(letter, G=80) == "B"
    assert run(letter, G=None) is None  # "" is empty
    assert run('prop("G") > 3', G=None) is False
    assert run('sum(prop("L")) / length(prop("L"))', L=[1, 2, 3]) == 2
    assert run('start(prop("D"))', D={"start": "2026-01-01", "end": "2026-02-01"}) == "2026-01-01"
    assert run('end(prop("D"))', D={"start": "2026-01-01", "end": None}) == "2026-01-01"
    assert run('contains(prop("Tags"), "b")', Tags=["a", "B"]) is True


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "Write a formula"),
        ("1 +", "ends too early"),
        ("1 2", "Unexpected"),
        ("score * 2", 'prop("Name")'),
        ("nope(1)", "Unknown function"),
        ("round()", "takes 1 to 2"),
        ("prop(1)", "prop() takes a property name"),
        ("1 # 2", "Unexpected character"),
        ("(" * 100 + "1" + ")" * 100, "nested too deeply"),
    ],
)
def test_errors_say_what_is_wrong(text, message):
    with pytest.raises(FormulaError, match=message.replace("(", r"\(").replace(")", r"\)")):
        compile_formula(text)


def test_references_and_renames():
    text = 'prop("A") + prop("B") * prop( "A" ) + "prop(\\"A\\")"'
    assert references(compile_formula(text)) == {"A", "B"}
    renamed = rename_property(text, "A", 'Say "hi"')
    assert references(compile_formula(renamed)) == {'Say "hi"', "B"}
    assert renamed.endswith('"prop(\\"A\\")"')  # text that only looks like a reference
