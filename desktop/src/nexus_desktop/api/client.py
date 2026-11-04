"""Synchronous REST client executed from Qt worker threads.

The desktop client never opens a database connection. Every read and state
transition is owned by the FastAPI service, including permission checks and
financial calculations.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from datetime import date
from typing import Any, TypeVar, cast

import httpx
from pydantic import BaseModel

from nexus_desktop.api.models import (
    Account,
    AttendancePage,
    AttendanceRecord,
    Compensation,
    Employee,
    EmployeePage,
    Holiday,
    JournalPage,
    LeaveBalance,
    LeaveRequest,
    LeaveRequestPage,
    LeaveType,
    Membership,
    MembershipPage,
    MeResponse,
    PayGroup,
    PayrollPeriod,
    PayrollResult,
    PayrollRun,
    Role,
    TokenPair,
    TrialBalance,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class ApiError(Exception):
    """A structured, user-presentable API failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "api_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details or {}


class NetworkError(Exception):
    """The API could not be reached or returned an unreadable response."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationExpired(ApiError):
    def __init__(self) -> None:
        super().__init__(
            "Your session has ended. Sign in again to continue.",
            status_code=401,
            code="session_expired",
        )


class NexusClient:
    """Thread-safe client for one signed-in desktop session."""

    def __init__(
        self,
        api_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        on_tokens_changed: Callable[[TokenPair | None], None] | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.api_url,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "Accept": "application/json",
                "User-Agent": "Nexus-Desktop/0.1.0",
            },
            transport=transport,
        )
        self._tokens: TokenPair | None = None
        self._lock = threading.RLock()
        self._on_tokens_changed = on_tokens_changed
        self._on_auth_expired: Callable[[], None] | None = None

    def set_auth_expired_callback(self, callback: Callable[[], None] | None) -> None:
        """Register a thread-safe notification for an unrecoverable session end."""

        with self._lock:
            self._on_auth_expired = callback

    def _notify_auth_expired(self) -> None:
        callback = self._on_auth_expired
        if callback is not None:
            callback()

    def close(self) -> None:
        self._http.close()

    @property
    def is_authenticated(self) -> bool:
        return self._tokens is not None

    def set_session(self, tokens: TokenPair) -> None:
        with self._lock:
            self._tokens = tokens
            if self._on_tokens_changed is not None:
                self._on_tokens_changed(tokens)

    def clear_session(self) -> None:
        with self._lock:
            self._tokens = None
            if self._on_tokens_changed is not None:
                self._on_tokens_changed(None)

    def login(self, organization_slug: str, email: str, password: str) -> TokenPair:
        payload = self._send(
            "POST",
            "/auth/login",
            json_body={
                "organization_slug": organization_slug.strip().lower(),
                "email": email.strip().lower(),
                "password": password,
            },
            authenticated=False,
        )
        tokens = TokenPair.model_validate(payload)
        self.set_session(tokens)
        return tokens

    def restore(self, refresh_token: str) -> TokenPair:
        with self._lock:
            self._tokens = TokenPair(
                access_token="",
                refresh_token=refresh_token,
                expires_in=0,
                refresh_expires_in=0,
            )
            try:
                payload = self._send(
                    "POST",
                    "/auth/refresh",
                    json_body={"refresh_token": refresh_token},
                    authenticated=False,
                )
            except Exception:
                self._tokens = None
                raise
            tokens = TokenPair.model_validate(payload)
            self._tokens = tokens
            if self._on_tokens_changed is not None:
                self._on_tokens_changed(tokens)
            return tokens

    def logout(self) -> None:
        with self._lock:
            tokens = self._tokens
            if tokens is not None:
                with contextlib.suppress(ApiError, NetworkError):
                    self._send(
                        "POST",
                        "/auth/logout",
                        json_body={"refresh_token": tokens.refresh_token},
                        authenticated=True,
                        allow_refresh=False,
                    )
            self.clear_session()

    def me(self) -> MeResponse:
        return self._parse(MeResponse, self._get("/auth/me"))

    def list_employees(
        self,
        company_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
        query: str | None = None,
        status: str | None = None,
    ) -> EmployeePage:
        params: dict[str, Any] = {
            "company_id": company_id,
            "page": page,
            "page_size": page_size,
        }
        if query:
            params["q"] = query
        if status:
            params["status"] = status
        return self._parse(EmployeePage, self._get("/employees", params=params))

    def create_employee(self, payload: dict[str, Any]) -> Employee:
        return self._parse(Employee, self._send("POST", "/employees", json_body=payload))

    def compensations(self, employee_id: str) -> list[Compensation]:
        payload = self._get(f"/employees/{employee_id}/compensation")
        return [Compensation.model_validate(item) for item in cast(list[Any], payload)]

    def list_roles(self) -> list[Role]:
        payload = self._get("/roles")
        return [Role.model_validate(item) for item in cast(list[Any], payload)]

    def list_users(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        query: str | None = None,
        status: str | None = None,
    ) -> MembershipPage:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if query:
            params["q"] = query
        if status:
            params["status"] = status
        return self._parse(MembershipPage, self._get("/users", params=params))

    def create_user(self, payload: dict[str, Any]) -> Membership:
        return self._parse(Membership, self._send("POST", "/users", json_body=payload))

    def set_user_status(self, membership_id: str, status: str) -> Membership:
        return self._parse(
            Membership,
            self._send("PATCH", f"/users/{membership_id}", json_body={"status": status}),
        )

    def add_role_assignment(
        self, membership_id: str, *, role_code: str, company_id: str | None
    ) -> Membership:
        return self._parse(
            Membership,
            self._send(
                "POST",
                f"/users/{membership_id}/role-assignments",
                json_body={"role_code": role_code, "company_id": company_id},
            ),
        )

    def remove_role_assignment(self, membership_id: str, assignment_id: str) -> Membership:
        return self._parse(
            Membership,
            self._send(
                "DELETE",
                f"/users/{membership_id}/role-assignments/{assignment_id}",
            ),
        )

    def list_pay_groups(self, company_id: str) -> list[PayGroup]:
        payload = self._get("/payroll/pay-groups", params={"company_id": company_id})
        return [PayGroup.model_validate(item) for item in cast(list[Any], payload)]

    def create_pay_group(self, *, company_id: str, code: str, name: str, currency: str) -> PayGroup:
        return self._parse(
            PayGroup,
            self._send(
                "POST",
                "/payroll/pay-groups",
                json_body={
                    "company_id": company_id,
                    "code": code,
                    "name": name,
                    "currency": currency,
                },
            ),
        )

    def list_payroll_periods(self, pay_group_id: str) -> list[PayrollPeriod]:
        payload = self._get("/payroll/periods", params={"pay_group_id": pay_group_id})
        return [PayrollPeriod.model_validate(item) for item in cast(list[Any], payload)]

    def create_payroll_period(
        self,
        *,
        pay_group_id: str,
        period_start: date,
        period_end: date,
        payment_date: date,
    ) -> PayrollPeriod:
        return self._parse(
            PayrollPeriod,
            self._send(
                "POST",
                "/payroll/periods",
                json_body={
                    "pay_group_id": pay_group_id,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "payment_date": payment_date.isoformat(),
                },
            ),
        )

    def list_payroll_runs(
        self, company_id: str, *, page: int = 1, page_size: int = 50
    ) -> list[PayrollRun]:
        payload = self._get(
            "/payroll/runs",
            params={"company_id": company_id, "page": page, "page_size": page_size},
        )
        return [PayrollRun.model_validate(item) for item in cast(list[Any], payload)]

    def create_payroll_run(
        self, *, company_id: str, pay_group_id: str, payroll_period_id: str
    ) -> PayrollRun:
        return self._parse(
            PayrollRun,
            self._send(
                "POST",
                "/payroll/runs",
                json_body={
                    "company_id": company_id,
                    "pay_group_id": pay_group_id,
                    "payroll_period_id": payroll_period_id,
                },
            ),
        )

    def payroll_results(self, run_id: str) -> list[PayrollResult]:
        payload = self._get(f"/payroll/runs/{run_id}/results")
        return [PayrollResult.model_validate(item) for item in cast(list[Any], payload)]

    def payroll_action(self, run_id: str, action: str) -> PayrollRun:
        if action not in {"calculate", "approve", "finalize", "post"}:
            raise ValueError(f"Unsupported payroll action: {action}")
        return self._parse(
            PayrollRun,
            self._send("POST", f"/payroll/runs/{run_id}/{action}"),
        )

    def list_leave_types(self, company_id: str, *, status: str | None = None) -> list[LeaveType]:
        params: dict[str, Any] = {"company_id": company_id}
        if status:
            params["status"] = status
        payload = self._get("/leave/types", params=params)
        return [LeaveType.model_validate(item) for item in cast(list[Any], payload)]

    def create_leave_type(
        self,
        *,
        company_id: str,
        code: str,
        name: str,
        annual_entitlement_days: str,
        max_carryover_days: str,
        is_paid: bool = True,
        requires_balance: bool = True,
        description: str | None = None,
    ) -> LeaveType:
        return self._parse(
            LeaveType,
            self._send(
                "POST",
                "/leave/types",
                json_body={
                    "company_id": company_id,
                    "code": code,
                    "name": name,
                    "description": description,
                    "is_paid": is_paid,
                    "requires_balance": requires_balance,
                    "annual_entitlement_days": annual_entitlement_days,
                    "max_carryover_days": max_carryover_days,
                },
            ),
        )

    def update_leave_type(self, leave_type_id: str, payload: dict[str, Any]) -> LeaveType:
        return self._parse(
            LeaveType,
            self._send("PATCH", f"/leave/types/{leave_type_id}", json_body=payload),
        )

    def list_holidays(self, company_id: str, *, year: int | None = None) -> list[Holiday]:
        params: dict[str, Any] = {"company_id": company_id}
        if year is not None:
            params["year"] = year
        payload = self._get("/leave/holidays", params=params)
        return [Holiday.model_validate(item) for item in cast(list[Any], payload)]

    def create_holiday(self, *, company_id: str, holiday_date: date, name: str) -> Holiday:
        return self._parse(
            Holiday,
            self._send(
                "POST",
                "/leave/holidays",
                json_body={
                    "company_id": company_id,
                    "holiday_date": holiday_date.isoformat(),
                    "name": name,
                },
            ),
        )

    def delete_holiday(self, holiday_id: str) -> None:
        self._send("DELETE", f"/leave/holidays/{holiday_id}")

    def list_leave_balances(self, employee_id: str, *, leave_year: int) -> list[LeaveBalance]:
        payload = self._get(
            "/leave/balances",
            params={"employee_id": employee_id, "leave_year": leave_year},
        )
        return [LeaveBalance.model_validate(item) for item in cast(list[Any], payload)]

    def initialize_leave_balances(self, *, employee_id: str, leave_year: int) -> list[LeaveBalance]:
        payload = self._send(
            "POST",
            "/leave/balances/initialize",
            json_body={"employee_id": employee_id, "leave_year": leave_year},
        )
        return [LeaveBalance.model_validate(item) for item in cast(list[Any], payload)]

    def adjust_leave_balance(self, balance_id: str, payload: dict[str, Any]) -> LeaveBalance:
        return self._parse(
            LeaveBalance,
            self._send("PATCH", f"/leave/balances/{balance_id}", json_body=payload),
        )

    def list_leave_requests(
        self,
        company_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
        employee_id: str | None = None,
        status: str | None = None,
        leave_year: int | None = None,
    ) -> LeaveRequestPage:
        params: dict[str, Any] = {
            "company_id": company_id,
            "page": page,
            "page_size": page_size,
        }
        if employee_id:
            params["employee_id"] = employee_id
        if status:
            params["status"] = status
        if leave_year is not None:
            params["leave_year"] = leave_year
        return self._parse(LeaveRequestPage, self._get("/leave/requests", params=params))

    def create_leave_request(
        self,
        *,
        employee_id: str,
        leave_type_id: str,
        start_date: date,
        end_date: date,
        reason: str,
    ) -> LeaveRequest:
        return self._parse(
            LeaveRequest,
            self._send(
                "POST",
                "/leave/requests",
                json_body={
                    "employee_id": employee_id,
                    "leave_type_id": leave_type_id,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "reason": reason,
                },
            ),
        )

    def leave_request_action(
        self, request_id: str, action: str, *, note: str | None = None
    ) -> LeaveRequest:
        if action not in {"approve", "reject", "cancel"}:
            raise ValueError(f"Unsupported leave action: {action}")
        return self._parse(
            LeaveRequest,
            self._send(
                "POST",
                f"/leave/requests/{request_id}/{action}",
                json_body={"note": note},
            ),
        )

    def list_attendance(
        self,
        company_id: str,
        *,
        start_date: date,
        end_date: date,
        page: int = 1,
        page_size: int = 100,
        employee_id: str | None = None,
    ) -> AttendancePage:
        params: dict[str, Any] = {
            "company_id": company_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "page": page,
            "page_size": page_size,
        }
        if employee_id:
            params["employee_id"] = employee_id
        return self._parse(AttendancePage, self._get("/attendance", params=params))

    def create_attendance(
        self,
        *,
        employee_id: str,
        work_date: date,
        status: str,
        note: str | None = None,
    ) -> AttendanceRecord:
        return self._parse(
            AttendanceRecord,
            self._send(
                "POST",
                "/attendance",
                json_body={
                    "employee_id": employee_id,
                    "work_date": work_date.isoformat(),
                    "status": status,
                    "note": note,
                },
            ),
        )

    def update_attendance(self, record_id: str, payload: dict[str, Any]) -> AttendanceRecord:
        return self._parse(
            AttendanceRecord,
            self._send("PATCH", f"/attendance/{record_id}", json_body=payload),
        )

    def list_accounts(self, company_id: str) -> list[Account]:
        payload = self._get("/accounting/accounts", params={"company_id": company_id})
        return [Account.model_validate(item) for item in cast(list[Any], payload)]

    def trial_balance(self, company_id: str, as_of: date) -> TrialBalance:
        return self._parse(
            TrialBalance,
            self._get(
                "/accounting/trial-balance",
                params={"company_id": company_id, "as_of": as_of.isoformat()},
            ),
        )

    def list_journals(self, company_id: str, *, page: int = 1, page_size: int = 50) -> JournalPage:
        return self._parse(
            JournalPage,
            self._get(
                "/accounting/journals",
                params={"company_id": company_id, "page": page, "page_size": page_size},
            ),
        )

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._send("GET", path, params=params)

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        authenticated: bool = True,
        allow_refresh: bool = True,
    ) -> Any:
        with self._lock:
            try:
                response = self._request(
                    method,
                    path,
                    json_body=json_body,
                    params=params,
                    authenticated=authenticated,
                )
            except AuthenticationExpired:
                self._notify_auth_expired()
                raise
            if response.status_code == 401 and authenticated and allow_refresh:
                try:
                    self._refresh_locked()
                except (ApiError, NetworkError):
                    self.clear_session()
                    self._notify_auth_expired()
                    raise AuthenticationExpired() from None
                response = self._request(
                    method,
                    path,
                    json_body=json_body,
                    params=params,
                    authenticated=True,
                )
                if response.status_code == 401:
                    self.clear_session()
                    self._notify_auth_expired()
                    raise AuthenticationExpired()
            return self._decode(response)

    def _refresh_locked(self) -> None:
        if self._tokens is None:
            raise AuthenticationExpired()
        response = self._request(
            "POST",
            "/auth/refresh",
            json_body={"refresh_token": self._tokens.refresh_token},
            authenticated=False,
        )
        if response.status_code >= 400:
            self._raise_api_error(response)
        tokens = TokenPair.model_validate(response.json())
        self._tokens = tokens
        if self._on_tokens_changed is not None:
            self._on_tokens_changed(tokens)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        authenticated: bool,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if authenticated:
            if self._tokens is None:
                raise AuthenticationExpired()
            headers["Authorization"] = f"Bearer {self._tokens.access_token}"
        try:
            return self._http.request(
                method,
                path,
                json=json_body,
                params=params,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise NetworkError("The Nexus API did not respond in time.") from exc
        except httpx.HTTPError as exc:
            raise NetworkError(
                "The Nexus API could not be reached. Check the server address and network."
            ) from exc

    def _decode(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            self._raise_api_error(response)
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise NetworkError("The Nexus API returned an unreadable response.") from exc

    @staticmethod
    def _raise_api_error(response: httpx.Response) -> None:
        message = "The request could not be completed."
        code = "api_error"
        details: dict[str, Any] = {}
        try:
            payload = response.json()
            if isinstance(payload, dict):
                data = cast(dict[str, object], payload)
                error = data.get("error")
                if isinstance(error, dict):
                    error_data = cast(dict[str, object], error)
                    message = str(error_data.get("message", message))
                    code = str(error_data.get("code", code))
                    raw_details = error_data.get("details")
                    if isinstance(raw_details, dict):
                        details = cast(dict[str, Any], raw_details)
        except ValueError:
            pass
        if response.status_code == 401:
            message = "Your session has ended. Sign in again to continue."
            code = "authentication_required"
        raise ApiError(
            message,
            status_code=response.status_code,
            code=code,
            details=details,
        )

    @staticmethod
    def _parse(model: type[ModelT], payload: Any) -> ModelT:
        try:
            return model.model_validate(payload)
        except ValueError as exc:
            raise NetworkError("The Nexus API returned data in an unexpected format.") from exc
