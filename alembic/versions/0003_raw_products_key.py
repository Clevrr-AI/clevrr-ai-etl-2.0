"""Key raw_shopify_products on (id, organization_id).

Legacy keyed it on `id` alone and its upsert overwrote `organization_id`, so two orgs on the same
shop took each other's rows. Every other raw Shopify table is keyed per org. The change runs only
while the key is still the single column; at cut-over on `public` it needs every row to have an
organization_id (on 2026-10-17 sandbox had no id shared by two orgs).

Revision ID: 0003_raw_products_key
Revises: 0002_data_tables
"""

from __future__ import annotations

from alembic import op

revision = "0003_raw_products_key"
down_revision = "0002_data_tables"
branch_labels = None
depends_on = None

UPGRADE = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE c.conname = 'raw_shopify_products_pkey' AND n.nspname = current_schema()
      AND cardinality(c.conkey) = 1
  ) THEN
    ALTER TABLE raw_shopify_products ALTER COLUMN organization_id SET NOT NULL;
    ALTER TABLE raw_shopify_products DROP CONSTRAINT raw_shopify_products_pkey;
    ALTER TABLE raw_shopify_products
      ADD CONSTRAINT raw_shopify_products_pkey PRIMARY KEY (id, organization_id);
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    raise NotImplementedError("The ETL schema only moves forward.")
