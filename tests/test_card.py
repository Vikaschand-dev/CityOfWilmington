import pytest

from wilmington_pay.card import (CardValidationError, build_card, detect_brand, luhn_valid, mask_pan,
                                 parse_expiry, validate_cvv)

NOW = 1_790_000_000.0  # Sept 2026


@pytest.mark.parametrize("pan,brand", [
    ("4111111111111111", "VISA"),
    ("5555555555554444", "MASTERCARD"),
    ("2223003122003222", "MASTERCARD"),
    ("378282246310005", "AMEX"),
    ("6011111111111117", "DISCOVER"),
])
def test_brand_and_luhn(pan, brand):
    assert luhn_valid(pan)
    assert detect_brand(pan) == brand


def test_luhn_rejects_typo():
    assert not luhn_valid("4111111111111112")


def test_expiry_rules():
    assert parse_expiry("1229", NOW) == (12, 2029)
    assert parse_expiry("0926", NOW) == (9, 2026)          # current month still valid
    for bad in ("0826", "1329", "12", "ab12"):
        with pytest.raises(CardValidationError):
            parse_expiry(bad, NOW)


def test_cvv_length_depends_on_brand():
    assert validate_cvv("1234", "AMEX") == "1234"
    with pytest.raises(CardValidationError):
        validate_cvv("123", "AMEX")
    with pytest.raises(CardValidationError):
        validate_cvv("1234", "VISA")


def test_card_data_never_reveals_itself():
    card = build_card("4111111111111111", "1229", "123", "19801", NOW)
    text = repr(card) + str(card) + f"{card}"
    assert "4111111111111111" not in text and "123" not in text.replace("1111", "")
    assert mask_pan(card.pan) == "************1111"


def test_validation_errors_do_not_echo_digits():
    with pytest.raises(CardValidationError) as exc:
        build_card("4111111111111112", "1229", "123", None, NOW)
    assert "4111" not in str(exc.value)
