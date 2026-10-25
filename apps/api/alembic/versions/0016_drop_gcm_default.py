"""drop the Consent Mode defaults setting

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-25

``gcm_default`` was stored on site, site group and organisation configs
but never applied: the banner always sets every Consent Mode type to
denied before its configuration arrives. Stored values are discarded.
Revisions keep the key in their snapshots, where it is ignored.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("site_configs", "site_group_configs", "org_configs")


def upgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "gcm_default")


def downgrade() -> None:
    for table in TABLES:
        op.add_column(
            table,
            sa.Column("gcm_default", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
