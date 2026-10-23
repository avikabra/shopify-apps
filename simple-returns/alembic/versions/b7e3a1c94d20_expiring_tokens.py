"""Add expiring-token storage to shops (refresh token + access token expiry).

Shopify no longer accepts non-expiring offline access tokens for the
Admin API. Installs now request expiring tokens (expiring=1), so the shop
row needs somewhere to keep the refresh token and the access token's
expiry.

Revision ID: b7e3a1c94d20
Revises: 9f2b7c1d4a55
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e3a1c94d20"
down_revision: Union[str, None] = "9f2b7c1d4a55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shops", sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True))
    op.add_column(
        "shops",
        sa.Column("access_token_expires_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shops", "access_token_expires_at")
    op.drop_column("shops", "refresh_token_enc")
