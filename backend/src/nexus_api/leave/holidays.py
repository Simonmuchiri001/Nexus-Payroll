"""Versioned Kenya national public-holiday calendar and working-day helper."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class NationalHoliday:
    date: date
    name: str


@dataclass(frozen=True, slots=True)
class NationalCalendar:
    version: str
    source_url: str
    verified_on: date
    checksum: str
    holidays: tuple[NationalHoliday, ...]


class _HolidayEntry(BaseModel):
    date: date
    name: str = Field(min_length=1)


class _CalendarDocument(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    verified_on: date
    holidays: list[_HolidayEntry] = Field(min_length=1)


def checksum_for_holidays(holidays: tuple[NationalHoliday, ...]) -> str:
    canonical = json.dumps(
        [{"date": holiday.date.isoformat(), "name": holiday.name} for holiday in holidays],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_national_calendar() -> NationalCalendar:
    path = Path(__file__).with_name("kenya_public_holidays.json")
    document = _CalendarDocument.model_validate_json(path.read_text(encoding="utf-8"))
    holidays = tuple(
        NationalHoliday(date=entry.date, name=entry.name) for entry in document.holidays
    )
    if len({holiday.date for holiday in holidays}) != len(holidays):
        raise ValueError("National holiday dataset contains duplicate dates.")
    return NationalCalendar(
        version=document.version,
        source_url=document.source_url,
        verified_on=document.verified_on,
        checksum=checksum_for_holidays(holidays),
        holidays=holidays,
    )


def working_dates(start: date, end: date, holidays: set[date]) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in holidays:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days
