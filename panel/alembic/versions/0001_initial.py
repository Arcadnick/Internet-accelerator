"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum("user", "admin", name="user_role"),
            nullable=False,
            server_default="user",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country_code", sa.String(8), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column(
            "status",
            sa.Enum(
                "provisioning", "active", "error", "disabled", name="node_status"
            ),
            nullable=False,
            server_default="provisioning",
        ),
        sa.Column("s2s_password", sa.String(128), nullable=False),
        sa.Column("s2s_sni", sa.String(255), nullable=False),
        sa.Column(
            "s2s_allow_insecure",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("panel_inbound_tag", sa.String(64), nullable=False),
        sa.Column("panel_outbound_tag", sa.String(64), nullable=False),
        sa.Column("panel_inbound_port", sa.Integer(), nullable=False),
        sa.Column("reality_short_id", sa.String(32), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("panel_inbound_tag"),
        sa.UniqueConstraint("panel_outbound_tag"),
        sa.UniqueConstraint("panel_inbound_port"),
    )

    op.create_table(
        "node_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "node_id",
            sa.Integer(),
            sa.ForeignKey("nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("traffic_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("xray_uuid", sa.String(36), nullable=False),
        sa.Column("xray_email", sa.String(128), nullable=False),
        sa.Column("sub_token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "traffic_used_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "traffic_limit_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "expired",
                "over_limit",
                "disabled",
                name="subscription_status",
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("xray_uuid"),
        sa.UniqueConstraint("xray_email"),
        sa.UniqueConstraint("sub_token"),
    )
    op.create_index("ix_subscriptions_xray_uuid", "subscriptions", ["xray_uuid"])
    op.create_index("ix_subscriptions_xray_email", "subscriptions", ["xray_email"])
    op.create_index("ix_subscriptions_sub_token", "subscriptions", ["sub_token"])

    op.create_table(
        "traffic_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.Integer(),
            sa.ForeignKey("nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("bytes_up", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_down", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_traffic_log_subscription_id", "traffic_log", ["subscription_id"])
    op.create_index("ix_traffic_log_node_id", "traffic_log", ["node_id"])
    op.create_index("ix_traffic_log_collected_at", "traffic_log", ["collected_at"])


def downgrade() -> None:
    op.drop_table("traffic_log")
    op.drop_table("subscriptions")
    op.drop_table("plans")
    op.drop_table("node_events")
    op.drop_table("nodes")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS subscription_status")
    op.execute("DROP TYPE IF EXISTS node_status")
    op.execute("DROP TYPE IF EXISTS user_role")
