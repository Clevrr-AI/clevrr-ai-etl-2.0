"""raw_meta_ad_creative: call_to_action and post_link.

Legacy's current transform (connectors/meta_ads/raw_sanitized_ad.sql) reads both, and its
loader creates them only on a fresh table (CREATE TABLE IF NOT EXISTS); the sandbox table and
the 2026-10-09 dump behind 0002 lack them. Types as in legacy's loader DDL.

Revision ID: 0004_meta_creative_links
Revises: 0003_raw_products_key
"""

from __future__ import annotations

from alembic import op

revision = "0004_meta_creative_links"
down_revision = "0003_raw_products_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE raw_meta_ad_creative ADD COLUMN IF NOT EXISTS call_to_action jsonb,"
        " ADD COLUMN IF NOT EXISTS post_link text"
    )


def downgrade() -> None:
    raise NotImplementedError("The ETL schema only moves forward.")
