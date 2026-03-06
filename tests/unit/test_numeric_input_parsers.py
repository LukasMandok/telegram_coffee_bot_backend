import pytest

from src.bot.message_flow_helpers import IntegerParser, MoneyParser


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("3.2", 3.2),
        ("3,2", 3.2),
        ("3,2 €", 3.2),
        ("3,2€", 3.2),
        ("3.2€", 3.2),
        ("3.2 €", 3.2),
        ("€3,2", 3.2),
        ("€3.2", 3.2),
        ("€ 3.2", 3.2),
        ("€ 3,2", 3.2),
        (",3", 0.3),
        (",2 €", 0.2),
        (" .2 ", 0.2),
        (" .2 €", 0.2),
        (" € .2", 0.2),
        (" € ,2", 0.2),
    ],
)
def test_money_parser_accepts_common_euro_formats(raw: str, expected: float):
    value = MoneyParser().parse(raw)
    assert value == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "abc", "€", "3..2", "3,,2", "3.2.1", "--2"])
def test_money_parser_rejects_invalid_formats(raw: str):
    assert MoneyParser().parse(raw) is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("42", 42),
        (" 42 ", 42),
        ("+7", 7),
        ("003", 3),
        ("-2", -2),
    ],
)
def test_integer_parser_parses_whole_numbers(raw: str, expected: int):
    assert IntegerParser().parse(raw) == expected


@pytest.mark.parametrize("raw", ["", "3.2", "3,2", "abc", "1 2", "€3"])
def test_integer_parser_rejects_non_integers(raw: str):
    assert IntegerParser().parse(raw) is None
