"""National holiday calendar and working-day coverage."""

from __future__ import annotations

import pytest

from nexus_api.leave.holidays import load_national_calendar, working_dates
from nexus_api.leave.service import working_days

pytestmark = pytest.mark.postgres

API = "/api/v1"


def test_national_calendar_dataset_is_well_formed() -> None:
    calendar = load_national_calendar()
    assert calendar.version
    assert calendar.source_url.startswith("http")
    assert calendar.verified_on is not None
    assert len(calendar.checksum) == 64
    assert len(calendar.holidays) >= 10
    dates = [holiday.date for holiday in calendar.holidays]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))
    for holiday in calendar.holidays:
        assert holiday.name


def test_working_days_exclude_weekends_and_holidays() -> None:
    from datetime import date

    # Fri 2026-01-02 to Mon 2026-01-05: Sat/Sun excluded
    days = working_dates(date(2026, 1, 2), date(2026, 1, 5), set())
    assert days == [date(2026, 1, 2), date(2026, 1, 5)]
    assert working_days(date(2026, 1, 2), date(2026, 1, 5), set()) == 2

    # Holiday on a weekday removes it
    days = working_dates(date(2026, 1, 2), date(2026, 1, 5), {date(2026, 1, 5)})
    assert days == [date(2026, 1, 2)]

    # Holiday on a weekend does not double-count
    days = working_dates(date(2026, 1, 2), date(2026, 1, 5), {date(2026, 1, 3)})
    assert days == [date(2026, 1, 2), date(2026, 1, 5)]

    # Year boundary
    days = working_dates(date(2025, 12, 31), date(2026, 1, 2), set())
    assert days == [date(2025, 12, 31), date(2026, 1, 1), date(2026, 1, 2)]


async def test_import_national_holidays_is_idempotent_and_preserves_custom(
    client, admin_session
) -> None:
    # Custom holiday on the first national date must survive import untouched.
    calendar = load_national_calendar()
    first = calendar.holidays[0].date
    custom = await client.post(
        f"{API}/leave/holidays",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "holiday_date": first.isoformat(),
            "name": "Company Founder Day",
        },
    )
    assert custom.status_code == 201, custom.text
    assert custom.json()["origin"] == "company"

    first_import = await client.post(
        f"{API}/leave/holidays/import-national",
        headers=admin_session.headers,
        params={"company_id": admin_session.company_id},
    )
    assert first_import.status_code == 200, first_import.text
    body = first_import.json()
    assert body["calendar_version"] == calendar.version
    assert body["imported"] == len(calendar.holidays) - 1
    assert body["skipped"] == 1

    second_import = await client.post(
        f"{API}/leave/holidays/import-national",
        headers=admin_session.headers,
        params={"company_id": admin_session.company_id},
    )
    assert second_import.status_code == 200, second_import.text
    assert second_import.json()["imported"] == 0
    assert second_import.json()["skipped"] == len(calendar.holidays)

    listing = await client.get(
        f"{API}/leave/holidays",
        headers=admin_session.headers,
        params={
            "company_id": admin_session.company_id,
            "year": first.year,
        },
    )
    assert listing.status_code == 200, listing.text
    by_date = {row["holiday_date"]: row for row in listing.json()}
    assert by_date[first.isoformat()]["name"] == "Company Founder Day"
    assert by_date[first.isoformat()]["origin"] == "company"
    national_rows = [row for row in listing.json() if row["origin"] == "national"]
    assert len(national_rows) == len(calendar.holidays) - 1
    for row in national_rows:
        assert row["calendar_version"] == calendar.version


async def test_import_national_holidays_rejects_cross_tenant_company(client, admin_session) -> None:
    import uuid

    other = str(uuid.uuid4())
    response = await client.post(
        f"{API}/leave/holidays/import-national",
        headers=admin_session.headers,
        params={"company_id": other},
    )
    # validate_company_access returns 403/404 for unknown companies; never 200.
    assert response.status_code in (403, 404), response.text
    assert response.status_code != 200
