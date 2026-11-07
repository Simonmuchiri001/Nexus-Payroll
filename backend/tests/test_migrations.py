"""Clean install, downgrade and re-upgrade of the Alembic schema."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url

from .conftest import TEST_DATABASE_URL, _assert_alembic_succeeded, _run_alembic

pytestmark = pytest.mark.postgres

SCHEMA_NAME = "nexus_migration_test"


def _sync_engine() -> Engine:
    return create_engine(make_url(TEST_DATABASE_URL).set(drivername="postgresql+psycopg"))


def _table_names(connection: Connection, schema: str) -> set[str]:
    result = connection.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
        ),
        {"schema": schema},
    )
    return {str(row[0]) for row in result}


@pytest.fixture
def migration_schema(postgres_database: None) -> Iterator[None]:
    sync_engine = _sync_engine()
    try:
        with sync_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA_NAME}" CASCADE'))
            connection.execute(text(f'CREATE SCHEMA "{SCHEMA_NAME}"'))
            connection.commit()
        yield
    finally:
        with sync_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA_NAME}" CASCADE'))
            connection.commit()
        sync_engine.dispose()


def test_migration_applies_cleanly_downgrades_and_reapplies(
    migration_schema: None,
) -> None:
    upgrade = _run_alembic("upgrade", "head", search_path=SCHEMA_NAME)
    _assert_alembic_succeeded(upgrade)

    sync_engine = _sync_engine()
    try:
        with sync_engine.connect() as connection:
            tables = _table_names(connection, SCHEMA_NAME)
            assert "alembic_version" in tables
            assert "organizations" in tables
            assert "users" in tables
            assert "payroll_runs" in tables
            assert "journal_lines" in tables
            assert "audit_events" in tables
            assert "leave_types" in tables
            assert "leave_balances" in tables
            assert "leave_requests" in tables
            assert "attendance_records" in tables
            assert "company_holidays" in tables
            assert "employee_loans" in tables
            assert "loan_installments" in tables
            assert "benefit_plans" in tables
            assert "benefit_enrollments" in tables
            assert "expense_categories" in tables
            assert "expense_claims" in tables
            assert "asset_categories" in tables
            assert "fixed_assets" in tables
            assert "asset_depreciations" in tables
            assert "asset_disposals" in tables
            assert len(tables) >= 33

            revision = connection.scalar(
                text(
                    f'SELECT version_num FROM "{SCHEMA_NAME}".alembic_version'  # noqa: S608
                )
            )
            assert revision == "e9023fa67460"

            trigger_exists = connection.scalar(
                text(
                    "SELECT count(DISTINCT trigger_name) FROM information_schema.triggers "
                    "WHERE trigger_schema = :schema "
                    "AND trigger_name = 'trg_audit_events_append_only'"
                ),
                {"schema": SCHEMA_NAME},
            )
            assert trigger_exists == 1
    finally:
        sync_engine.dispose()

    downgrade = _run_alembic("downgrade", "base", search_path=SCHEMA_NAME)
    _assert_alembic_succeeded(downgrade)

    sync_engine = _sync_engine()
    try:
        with sync_engine.connect() as connection:
            remaining = _table_names(connection, SCHEMA_NAME)
            assert remaining <= {"alembic_version"}
            assert "organizations" not in remaining
            assert "payroll_runs" not in remaining
    finally:
        sync_engine.dispose()

    reupgrade = _run_alembic("upgrade", "head", search_path=SCHEMA_NAME)
    _assert_alembic_succeeded(reupgrade)

    sync_engine = _sync_engine()
    try:
        with sync_engine.connect() as connection:
            tables = _table_names(connection, SCHEMA_NAME)
            revision = connection.scalar(
                text(
                    f'SELECT version_num FROM "{SCHEMA_NAME}".alembic_version'  # noqa: S608
                )
            )
            assert revision == "e9023fa67460"
            assert "organizations" in tables
            assert "journal_lines" in tables
            assert "employee_loans" in tables
            assert "benefit_enrollments" in tables
            assert "expense_claims" in tables
            assert "fixed_assets" in tables
            assert "asset_disposals" in tables
    finally:
        sync_engine.dispose()
