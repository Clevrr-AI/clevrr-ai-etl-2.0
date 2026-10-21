"""raw_shopify_products: a unique key per org, (id, organization_id).

Legacy keyed the table on `id` alone and its upsert overwrote `organization_id`, so two orgs on
the same shop took each other's rows. v2 upserts on (id, organization_id). The primary key on
`id` stays, so legacy's own loader (`ON CONFLICT (id)`) keeps working on the shared sandbox
tables; the new unique index serves v2's upsert. Both keys together still mean one shop id
cannot belong to two orgs, as in legacy.

Revision ID: 0003_raw_products_key
Revises: 0002_data_tables
"""

from __future__ import annotations

from alembic import op

revision = "0003_raw_products_key"
down_revision = "0002_data_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_raw_shopify_products_id_org"
        " ON raw_shopify_products (id, organization_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_raw_shopify_products_id_org")
