"""Helpers for working with day-of-week values."""
from __future__ import annotations

from typing import Iterable, List, Sequence, Set, Union

DAY_NAME_TO_ISO: dict[str, int] = {
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
    "sun": 7,
}

ISO_TO_DAY_NAME: dict[int, str] = {value: key for key, value in DAY_NAME_TO_ISO.items()}


def normalize_day_name(value: str) -> str:
    """Normalize an input day string and ensure it is valid."""
    candidate = value.strip().lower()
    if candidate not in DAY_NAME_TO_ISO:
        raise ValueError(f"Invalid day of week value: {value!r}")
    return candidate


def normalize_day_list(values: Sequence[str]) -> List[str]:
    """Normalize a sequence of day names."""
    return [normalize_day_name(value) for value in values]


def decode_day_list(raw: Iterable[Union[str, int]]) -> List[str]:
    """Convert stored database values into normalized day names.

    Handles legacy integer payloads by mapping ISO weekday numbers (1-7) to their
    three-letter abbreviations.
    """
    normalized: list[str] = []
    for value in raw:
        if isinstance(value, str):
            try:
                normalized.append(normalize_day_name(value))
            except ValueError:
                continue
        elif isinstance(value, int):
            name = ISO_TO_DAY_NAME.get(value)
            if name:
                normalized.append(name)
        else:
            # Unsupported type; skip
            continue
    return normalized


def day_names_to_iso(days: Sequence[str]) -> Set[int]:
    """Convert normalized day names to ISO weekday numbers."""
    return {DAY_NAME_TO_ISO[day] for day in days}


__all__ = [
    "DAY_NAME_TO_ISO",
    "ISO_TO_DAY_NAME",
    "normalize_day_list",
    "decode_day_list",
    "day_names_to_iso",
]
