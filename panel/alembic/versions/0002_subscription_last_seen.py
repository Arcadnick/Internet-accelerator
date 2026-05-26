"""subscription.last_seen_at for online detection

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_subscriptions_last_seen_at",
        "subscriptions",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_subscriptions_last_seen_at", table_name="subscriptions")
    op.drop_column("subscriptions", "last_seen_at")
