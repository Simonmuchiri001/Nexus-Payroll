"""Versioned Kenyan statutory calculation engine.

The engine is deterministic and side-effect free.  Rates are never embedded in
this module; they are supplied by an effective-dated rule set whose JSON payload
is validated by the models below and snapshotted onto every payroll run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

CENT = Decimal("0.01")
ENGINE_VERSION = "KE-1.0.0"


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class TaxBand(BaseModel):
    upper: Decimal | None = Field(default=None, gt=0)
    rate: Decimal = Field(ge=0, le=1)


class NssfRule(BaseModel):
    employee_rate: Decimal = Field(ge=0, le=1)
    employer_rate: Decimal = Field(ge=0, le=1)
    lower_earnings_limit: Decimal = Field(ge=0)
    upper_earnings_limit: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_limits(self) -> NssfRule:
        if self.upper_earnings_limit < self.lower_earnings_limit:
            raise ValueError("NSSF upper limit must be at least the lower limit")
        return self


class ShifRule(BaseModel):
    rate: Decimal = Field(ge=0, le=1)
    minimum_monthly: Decimal = Field(ge=0)


class HousingLevyRule(BaseModel):
    employee_rate: Decimal = Field(ge=0, le=1)
    employer_rate: Decimal = Field(ge=0, le=1)


class NitaRule(BaseModel):
    enabled: bool
    monthly_amount_per_employee: Decimal = Field(ge=0)


class PensionRule(BaseModel):
    relief_cap_monthly: Decimal = Field(ge=0)


class KenyaStatutoryRules(BaseModel):
    """Validated interpretation of the statutory rules for one effective period."""

    jurisdiction_code: Literal["KE"] = "KE"
    currency: Literal["KES"] = "KES"
    paye_bands: list[TaxBand] = Field(min_length=1)
    personal_relief_monthly: Decimal = Field(ge=0)
    nssf: NssfRule
    shif: ShifRule
    housing_levy: HousingLevyRule
    nita: NitaRule
    pension: PensionRule
    post_retirement_medical_cap_monthly: Decimal = Field(ge=0)
    mortgage_interest_cap_monthly: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_bands(self) -> KenyaStatutoryRules:
        uppers = [band.upper for band in self.paye_bands]
        if uppers[-1] is not None:
            raise ValueError("The final PAYE band must be open-ended (upper=null)")
        finite = [upper for upper in uppers[:-1] if upper is not None]
        if any(value is None for value in uppers[:-1]):
            raise ValueError("Only the final PAYE band may be open-ended")
        if finite != sorted(finite) or len(set(finite)) != len(finite):
            raise ValueError("PAYE band ceilings must be strictly increasing")
        return self


@dataclass(frozen=True, slots=True)
class StatutoryInput:
    gross: Decimal
    pension_contribution: Decimal = Decimal("0.00")
    post_retirement_medical: Decimal = Decimal("0.00")
    mortgage_interest: Decimal = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class StatutoryOutput:
    taxable_income: Decimal
    paye: Decimal
    nssf_employee: Decimal
    nssf_employer: Decimal
    shif: Decimal
    housing_employee: Decimal
    housing_employer: Decimal
    nita: Decimal
    total_employee_statutory: Decimal
    total_employer_statutory: Decimal
    allowed_pension: Decimal
    allowed_medical: Decimal
    allowed_mortgage: Decimal


def calculate_kenya(rules: KenyaStatutoryRules, values: StatutoryInput) -> StatutoryOutput:
    gross = money(max(values.gross, Decimal("0.00")))
    pension = money(
        min(max(values.pension_contribution, Decimal("0.00")), rules.pension.relief_cap_monthly)
    )
    medical = money(
        min(
            max(values.post_retirement_medical, Decimal("0.00")),
            rules.post_retirement_medical_cap_monthly,
        )
    )
    mortgage = money(
        min(max(values.mortgage_interest, Decimal("0.00")), rules.mortgage_interest_cap_monthly)
    )

    pensionable = min(gross, rules.nssf.upper_earnings_limit)
    tier_one = min(pensionable, rules.nssf.lower_earnings_limit)
    tier_two = max(Decimal("0.00"), pensionable - rules.nssf.lower_earnings_limit)
    nssf_employee = money((tier_one + tier_two) * rules.nssf.employee_rate)
    nssf_employer = money((tier_one + tier_two) * rules.nssf.employer_rate)

    shif = (
        money(max(rules.shif.minimum_monthly, gross * rules.shif.rate))
        if gross > 0
        else Decimal("0.00")
    )
    housing_employee = money(gross * rules.housing_levy.employee_rate)
    housing_employer = money(gross * rules.housing_levy.employer_rate)

    taxable = max(
        Decimal("0.00"),
        money(gross - nssf_employee - shif - housing_employee - pension - medical - mortgage),
    )
    tax_before_relief = _band_tax(rules, taxable)
    paye = money(max(Decimal("0.00"), tax_before_relief - rules.personal_relief_monthly))

    nita = rules.nita.monthly_amount_per_employee if rules.nita.enabled else Decimal("0.00")
    total_employee = money(nssf_employee + shif + housing_employee + paye)
    total_employer = money(nssf_employer + housing_employer + nita)

    return StatutoryOutput(
        taxable_income=taxable,
        paye=paye,
        nssf_employee=nssf_employee,
        nssf_employer=nssf_employer,
        shif=shif,
        housing_employee=housing_employee,
        housing_employer=housing_employer,
        nita=money(nita),
        total_employee_statutory=total_employee,
        total_employer_statutory=total_employer,
        allowed_pension=pension,
        allowed_medical=medical,
        allowed_mortgage=mortgage,
    )


def _band_tax(rules: KenyaStatutoryRules, taxable: Decimal) -> Decimal:
    lower = Decimal("0.00")
    total = Decimal("0.00")
    for band in rules.paye_bands:
        upper = band.upper if band.upper is not None else taxable
        width = max(Decimal("0.00"), min(taxable, upper) - lower)
        total += width * band.rate
        lower = upper
        if taxable <= upper:
            break
    return money(total)


@dataclass(frozen=True, slots=True)
class RuleSeed:
    name: str
    version: str
    effective_from: date
    rules: dict[str, Any]
    source_urls: list[str]
    checksum: str


class RuleSeedDocument(BaseModel):
    name: str
    version: str
    effective_from: date
    source_urls: list[str]
    rules: dict[str, Any]


def load_kenya_rule_seed() -> RuleSeed:
    path = Path(__file__).with_name("rules") / "kenya_2026.json"
    document = RuleSeedDocument.model_validate_json(path.read_text(encoding="utf-8"))
    KenyaStatutoryRules.model_validate(document.rules)
    return RuleSeed(
        name=document.name,
        version=document.version,
        effective_from=document.effective_from,
        rules=document.rules,
        source_urls=document.source_urls,
        checksum=checksum_for_rules(document.rules),
    )


def rules_for_snapshot(rule_row_rules: dict[str, Any]) -> KenyaStatutoryRules:
    return KenyaStatutoryRules.model_validate(rule_row_rules)


def checksum_for_rules(rules: dict[str, Any]) -> str:
    canonical = json.dumps(rules, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def empty_statutory_output() -> StatutoryOutput:
    return StatutoryOutput(
        taxable_income=Decimal("0.00"),
        paye=Decimal("0.00"),
        nssf_employee=Decimal("0.00"),
        nssf_employer=Decimal("0.00"),
        shif=Decimal("0.00"),
        housing_employee=Decimal("0.00"),
        housing_employer=Decimal("0.00"),
        nita=Decimal("0.00"),
        total_employee_statutory=Decimal("0.00"),
        total_employer_statutory=Decimal("0.00"),
        allowed_pension=Decimal("0.00"),
        allowed_medical=Decimal("0.00"),
        allowed_mortgage=Decimal("0.00"),
    )
