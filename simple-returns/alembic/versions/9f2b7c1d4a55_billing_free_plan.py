"""billing: free plan + shopify subscription fields

Revision ID: 9f2b7c1d4a55
Revises: c2c34f425330
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9f2b7c1d4a55'
down_revision: Union[str, None] = 'c2c34f425330'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the FREE value to the plan enum. Postgres-only; SQLite ignores DDL
    # it doesn't understand, so guard with a bind check.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
        # older Postgres; commit the current transaction first.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE plan ADD VALUE IF NOT EXISTS 'FREE'")
    op.add_column(
        "shops",
        sa.Column("app_subscription_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "shops",
        sa.Column("subscription_status", sa.String(32), nullable=True),
    )
    # New installs default to the Free plan going forward.
    op.execute("UPDATE shops SET plan = 'STARTER' WHERE plan IS NULL")


def downgrade() -> None:
    op.drop_column("shops", "subscription_status")
    op.drop_column("shops", "app_subscription_id")
    # NOTE: Postgres cannot remove an enum value; 'FREE' stays in the type.
