"""add company holiday origin and payroll attendance columns

Revision ID: 516c413f752e
Revises: 33004b139d69
Create Date: 2026-11-07 20:38:12.594080
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "516c413f752e"
down_revision: str | None = "33004b139d69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "company_holidays",
        sa.Column("origin", sa.String(length=20), server_default="company", nullable=False),
    )
    op.add_column(
        "company_holidays", sa.Column("calendar_version", sa.String(length=30), nullable=True)
    )
    op.create_check_constraint(
        "origin_valid", "company_holidays", "origin IN ('company', 'national')"
    )


def downgrade() -> None:
    op.drop_constraint("origin_valid", "company_holidays", type_="check")
    op.drop_column("company_holidays", "calendar_version")
    op.drop_column("company_holidays", "origin")
