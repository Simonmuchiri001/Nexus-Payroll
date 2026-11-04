"""Deterministic tests for the versioned Kenyan statutory engine."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from nexus_api.payroll.statutory import (
    KenyaStatutoryRules,
    StatutoryInput,
    calculate_kenya,
    load_kenya_rule_seed,
    money,
    rules_for_snapshot,
)

RULES = KenyaStatutoryRules.model_validate(load_kenya_rule_seed().rules)


def test_rule_seed_is_versioned_and_checksum_is_pinned() -> None:
    seed = load_kenya_rule_seed()

    assert seed.version == "KE-2026.1"
    assert seed.effective_from.isoformat() == "2025-02-01"
    assert seed.checksum == ("9fb9c69a1e2e97fb78157efe679c8fea8656b17fb43ed1aa467e052b87b8a219")
    assert all(url.startswith("https://") for url in seed.source_urls)
    assert "kra.go.ke" in seed.source_urls[0]
    assert "sha.go.ke" in seed.source_urls[1]
    assert "nssf.or.ke" in seed.source_urls[2]


def test_rule_snapshot_round_trips_through_validated_model() -> None:
    snapshot = rules_for_snapshot(load_kenya_rule_seed().rules)

    assert snapshot == RULES


def test_paye_nssf_shif_and_housing_for_mid_level_salary() -> None:
    result = calculate_kenya(RULES, StatutoryInput(gross=Decimal("100000.00")))

    assert result.nssf_employee == Decimal("4320.00")
    assert result.nssf_employer == Decimal("4320.00")
    assert result.shif == Decimal("2750.00")
    assert result.housing_employee == Decimal("1500.00")
    assert result.housing_employer == Decimal("1500.00")
    assert result.taxable_income == Decimal("91430.00")
    assert result.paye == Decimal("19812.35")
    assert result.total_employee_statutory == Decimal("28382.35")
    assert result.total_employer_statutory == Decimal("5820.00")
    assert result.nita == Decimal("0.00")


def test_nssf_and_shif_minimums_apply_to_low_salary() -> None:
    result = calculate_kenya(RULES, StatutoryInput(gross=Decimal("5000.00")))

    assert result.nssf_employee == Decimal("300.00")
    assert result.nssf_employer == Decimal("300.00")
    assert result.shif == Decimal("300.00")
    assert result.housing_employee == Decimal("75.00")
    assert result.taxable_income == Decimal("4325.00")
    assert result.paye == Decimal("0.00")


def test_zero_gross_produces_no_statutory_liability() -> None:
    result = calculate_kenya(RULES, StatutoryInput(gross=Decimal("0.00")))

    assert result.taxable_income == Decimal("0.00")
    assert result.paye == Decimal("0.00")
    assert result.nssf_employee == Decimal("0.00")
    assert result.shif == Decimal("0.00")
    assert result.housing_employee == Decimal("0.00")


def test_top_paye_band_and_relief() -> None:
    result = calculate_kenya(RULES, StatutoryInput(gross=Decimal("1000000.00")))

    assert result.taxable_income == Decimal("953180.00")
    assert result.paye == Decimal("293496.35")


def test_pension_relief_is_capped_at_thirty_thousand() -> None:
    result = calculate_kenya(
        RULES,
        StatutoryInput(gross=Decimal("100000.00"), pension_contribution=Decimal("50000.00")),
    )

    assert result.allowed_pension == Decimal("30000.00")
    assert result.taxable_income == Decimal("61430.00")
    assert result.paye == Decimal("10812.35")


def test_medical_and_mortgage_reliefs_are_capped() -> None:
    result = calculate_kenya(
        RULES,
        StatutoryInput(
            gross=Decimal("100000.00"),
            post_retirement_medical=Decimal("20000.00"),
            mortgage_interest=Decimal("40000.00"),
        ),
    )

    assert result.allowed_medical == Decimal("15000.00")
    assert result.allowed_mortgage == Decimal("30000.00")
    assert result.taxable_income == Decimal("46430.00")


def test_rounding_is_half_up_to_the_cent() -> None:
    assert money(Decimal("10.005")) == Decimal("10.01")
    assert money(Decimal("10.004")) == Decimal("10.00")


def test_negative_deductions_are_clamped() -> None:
    result = calculate_kenya(
        RULES,
        StatutoryInput(
            gross=Decimal("10000.00"),
            pension_contribution=Decimal("-100.00"),
            mortgage_interest=Decimal("-500.00"),
        ),
    )

    assert result.allowed_pension == Decimal("0.00")
    assert result.allowed_mortgage == Decimal("0.00")


def test_invalid_band_structure_is_rejected() -> None:
    payload = load_kenya_rule_seed().rules
    payload["paye_bands"] = [
        {"upper": "24000.00", "rate": "0.10"},
        {"upper": "20000.00", "rate": "0.25"},
        {"upper": None, "rate": "0.35"},
    ]

    with pytest.raises(ValidationError):
        KenyaStatutoryRules.model_validate(payload)


def test_open_ended_final_band_is_required() -> None:
    payload = load_kenya_rule_seed().rules
    payload["paye_bands"] = [{"upper": "24000.00", "rate": "0.10"}]

    with pytest.raises(ValidationError):
        KenyaStatutoryRules.model_validate(payload)


def test_nssf_upper_limit_cannot_be_below_lower_limit() -> None:
    payload = load_kenya_rule_seed().rules
    payload["nssf"] = {
        "employee_rate": "0.06",
        "employer_rate": "0.06",
        "lower_earnings_limit": "8000.00",
        "upper_earnings_limit": "7000.00",
    }

    with pytest.raises(ValidationError):
        KenyaStatutoryRules.model_validate(payload)
