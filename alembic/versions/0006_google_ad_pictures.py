"""raw_google_ads_ad_creative: image_url and youtube_video_id.

The creative snapshot now resolves the picture of display, Demand Gen, app and video ads
through the account's image and YouTube assets. The advertising transform writes `image_url`
to `advertising.thumbnail_url` and `advertising.image_url`, like Meta.

Revision ID: 0006_google_ad_pictures
Revises: 0005_google_ad_primary_status
"""

from __future__ import annotations

from alembic import op

revision = "0006_google_ad_pictures"
down_revision = "0005_google_ad_primary_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE raw_google_ads_ad_creative ADD COLUMN IF NOT EXISTS image_url text,"
        " ADD COLUMN IF NOT EXISTS youtube_video_id text"
    )


def downgrade() -> None:
    raise NotImplementedError("The ETL schema only moves forward.")
