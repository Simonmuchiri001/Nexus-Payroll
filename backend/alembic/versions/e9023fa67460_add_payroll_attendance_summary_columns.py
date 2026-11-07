"""add payroll attendance summary columns

Revision ID: e9023fa67460
Revises: 516c413f752e
Create Date: 2026-11-07 20:53:51.393991
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9023fa67460"
down_revision: str | None = "516c413f752e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payroll_results",
        sa.Column("working_days", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "payroll_results", sa.Column("paid_days", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "payroll_results",
        sa.Column("unpaid_days", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "payroll_results",
        sa.Column(
            "unpaid_deduction",
            sa.Numeric(precision=20, scale=2),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "payroll_runs",
        sa.Column(
            "unpaid_deduction_total",
            sa.Numeric(precision=20, scale=2),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "attendance_non_negative",
        "payroll_results",
        "working_days >= 0 AND paid_days >= 0 AND unpaid_days >= 0 AND unpaid_deduction >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("attendance_non_negative", "payroll_results", type_="check")
    op.drop_column("payroll_runs", "unpaid_deduction_total")
    op.drop_column("payroll_results", "unpaid_deduction")
    op.drop_column("payroll_results", "unpaid_days")
    op.drop_column("payroll_results", "paid_days")
    op.drop_column("payroll_results", "working_days")
