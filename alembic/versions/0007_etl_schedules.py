"""etl_schedules: how often each org's connector runs (`etl tick`, etl/core/schedules.py).

One row per (org, source). interval_minutes is one of the values that divide a day (60 to
1440); an org with no row runs daily, as the nightly jobs did. `enabled = false` pauses the
org for that source. The ETL owns this table; the backend may read it later.

Revision ID: 0007_etl_schedules
Revises: 0006_google_ad_pictures
"""

from __future__ import annotations

from alembic import op

revision = "0007_etl_schedules"
down_revision = "0006_google_ad_pictures"
branch_labels = None
depends_on = None

_UP = (
    """
    CREATE TABLE IF NOT EXISTS etl_schedules (
        org_id text NOT NULL,
        connector_key text NOT NULL,
        interval_minutes integer NOT NULL
            CHECK (interval_minutes IN (60, 120, 180, 240, 360, 480, 720, 1440)),
        enabled boolean NOT NULL DEFAULT true,
        note text,
        updated_at timestamp with time zone NOT NULL DEFAULT now(),
        PRIMARY KEY (org_id, connector_key)
    )
    """,
)

_DOWN = ("DROP TABLE IF EXISTS etl_schedules",)


def upgrade() -> None:
    for statement in _UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWN:
        op.execute(statement)
