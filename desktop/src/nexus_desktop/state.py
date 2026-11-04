"""Signed-in application state shared by the workspace pages."""

from __future__ import annotations

from dataclasses import dataclass

from nexus_desktop.api.client import NexusClient
from nexus_desktop.api.models import CompanySummary, MeResponse


@dataclass(slots=True)
class ApplicationSession:
    client: NexusClient
    me: MeResponse
    api_url: str
    organization_slug: str
    email: str
    selected_company_id: str

    @property
    def company(self) -> CompanySummary:
        for company in self.me.companies:
            if str(company.id) == self.selected_company_id:
                return company
        return self.me.companies[0]

    def select_company(self, company_id: str) -> None:
        if any(str(company.id) == company_id for company in self.me.companies):
            self.selected_company_id = company_id

    def can(self, permission: str) -> bool:
        return self.me.can(permission)
