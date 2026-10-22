"""Card-data validation. CardData is deliberately hard to leak:
- repr/str are masked, so it cannot end up in a log line by accident
- it is never serialised, persisted or returned from any API
"""
from __future__ import annotations

import time
from dataclasses import dataclass

BRAND_RULES: list[tuple[str, tuple[str, ...], tuple[int, ...]]] = [
    ("AMEX", ("34", "37"), (15,)),
    ("VISA", ("4",), (13, 16, 19)),
    ("MASTERCARD", tuple(str(p) for p in range(51, 56)) + tuple(str(p) for p in range(2221, 2721)), (16,)),
    ("DISCOVER", ("6011", "65") + tuple(str(p) for p in range(644, 650)), (16, 19)),
]


class CardValidationError(ValueError):
    """Raised with a caller-safe message (never includes the digits)."""


def luhn_valid(pan: str) -> bool:
    if not pan.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(pan)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect_brand(pan: str) -> str | None:
    for brand, prefixes, lengths in BRAND_RULES:
        if len(pan) in lengths and pan.startswith(prefixes):
            return brand
    return None


def mask_pan(pan: str) -> str:
    return f"{'*' * max(len(pan) - 4, 0)}{pan[-4:]}" if pan else ""


@dataclass(frozen=True, repr=False)
class CardData:
    pan: str
    exp_month: int
    exp_year: int          # 4-digit
    cvv: str
    postal_code: str | None = None

    @property
    def last4(self) -> str:
        return self.pan[-4:]

    @property
    def brand(self) -> str | None:
        return detect_brand(self.pan)

    def __repr__(self) -> str:  # never print card data
        return f"CardData(pan={mask_pan(self.pan)}, exp=**/**, cvv=***)"

    __str__ = __repr__


def validate_pan(pan: str) -> str:
    pan = pan.strip()
    if not pan.isdigit() or not 13 <= len(pan) <= 19:
        raise CardValidationError("That card number was not the right length.")
    if not luhn_valid(pan):
        raise CardValidationError("That card number is not valid.")
    if detect_brand(pan) is None:
        raise CardValidationError("We accept Visa, Mastercard, American Express and Discover.")
    return pan


def parse_expiry(mmyy: str, now: float | None = None) -> tuple[int, int]:
    mmyy = mmyy.strip()
    if len(mmyy) != 4 or not mmyy.isdigit():
        raise CardValidationError("Please enter the expiration as four digits, month then year.")
    month, year = int(mmyy[:2]), 2000 + int(mmyy[2:])
    if not 1 <= month <= 12:
        raise CardValidationError("That expiration month is not valid.")
    t = time.gmtime(now if now is not None else time.time())
    if (year, month) < (t.tm_year, t.tm_mon):
        raise CardValidationError("That card has expired.")
    if year > t.tm_year + 20:
        raise CardValidationError("That expiration year is not valid.")
    return month, year


def validate_cvv(cvv: str, brand: str | None) -> str:
    expected = 4 if brand == "AMEX" else 3
    if not cvv.isdigit() or len(cvv) != expected:
        raise CardValidationError(f"The security code should be {expected} digits.")
    return cvv


def validate_postal(zip5: str) -> str:
    if len(zip5) != 5 or not zip5.isdigit():
        raise CardValidationError("Please enter a five digit billing ZIP code.")
    return zip5


def build_card(pan: str, mmyy: str, cvv: str, postal: str | None = None, now: float | None = None) -> CardData:
    pan = validate_pan(pan)
    month, year = parse_expiry(mmyy, now)
    cvv = validate_cvv(cvv, detect_brand(pan))
    if postal is not None:
        postal = validate_postal(postal)
    return CardData(pan=pan, exp_month=month, exp_year=year, cvv=cvv, postal_code=postal)
