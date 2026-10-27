"""raw_google_ads_ad_group_ad: ad_primary_status.

The Google Ads fetch now reads ad_group_ad.primary_status, the serving status of an ad
(ELIGIBLE, LIMITED, PAUSED, NOT_ELIGIBLE, PENDING, REMOVED). The transform writes it to
advertising.ad_status, so a Google ad's status means the same as a Meta ad's effective_status.

Revision ID: 0005_google_ad_primary_status
Revises: 0004_meta_creative_links
"""

from __future__ import annotations

from alembic import op

revision = "0005_google_ad_primary_status"
down_revision = "0004_meta_creative_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE raw_google_ads_ad_group_ad ADD COLUMN IF NOT EXISTS ad_primary_status text"
    )


def downgrade() -> None:
    raise NotImplementedError("The ETL schema only moves forward.")
