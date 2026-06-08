from __future__ import annotations

import re


def digits_only(value: str | None) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_phone_number(value: str | None, *, default_country_code: str = "+91") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    default_country_code = (default_country_code or "+91").strip()
    default_digits = digits_only(default_country_code)
    digits = digits_only(raw)
    if not digits:
        return ""

    if raw.startswith("+"):
        return f"+{digits}"

    if raw.startswith("00") and len(digits) > 2:
        return f"+{digits[2:]}"

    if default_digits and len(digits) == 10:
        return f"+{default_digits}{digits}"

    if default_digits and digits.startswith(default_digits) and len(digits) >= 10:
        return f"+{digits}"

    if len(digits) > 10:
        return f"+{digits}"

    return f"+{default_digits}{digits}" if default_digits else f"+{digits}"


def phone_key(value: str | None) -> str:
    digits = digits_only(value)
    return digits[-10:] if len(digits) >= 10 else digits


def numbers_match(left: str | None, right: str | None) -> bool:
    left_key = phone_key(left)
    right_key = phone_key(right)
    return bool(left_key and right_key and left_key == right_key)


def mask_phone(value: str | None) -> str:
    digits = digits_only(value)
    if len(digits) <= 4:
        return value or ""
    return f"{'*' * max(len(digits) - 4, 0)}{digits[-4:]}"

