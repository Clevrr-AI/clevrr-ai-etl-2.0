"""The run ledger: etl_runs (+ heartbeat and pipeline columns), etl_steps, etl_watermarks.

Written to work in both places: a fresh schema (sandbox `etl_v2`, everything created) and, at
cut-over, `public`, where legacy's etl_runs and etl_watermarks already exist (only the new
columns, the steps table and the indexes are added; nothing legacy reads is changed).

Revision ID: 0001_run_ledger
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_run_ledger"
down_revision = None
branch_labels = None
depends_on = None

_UP = (
    # Legacy's columns, exactly (backend-2.0 /organizations/me/sync-runs reads them).
    """
    CREATE TABLE IF NOT EXISTS etl_runs (
        job_id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
        org_id character varying(255) NOT NULL,
        connector_key character varying NOT NULL,
        mode character varying NOT NULL,
        window_start timestamp with time zone,
        window_end timestamp with time zone,
        status character varying NOT NULL,
        attempt integer DEFAULT 1,
        resume_token character varying,
        records_in bigint,
        records_out bigint,
        raw_uri character varying,
        xform_uri character varying,
        summary_json jsonb,
        error_type character varying,
        error_message character varying,
        created_at timestamp with time zone DEFAULT now(),
        updated_at timestamp with time zone DEFAULT now()
    )
    """,
    "ALTER TABLE etl_runs ADD COLUMN IF NOT EXISTS pipeline text",
    "ALTER TABLE etl_runs ADD COLUMN IF NOT EXISTS heartbeat_at timestamp with time zone",
    "ALTER TABLE etl_runs ADD COLUMN IF NOT EXISTS finished_at timestamp with time zone",
    "ALTER TABLE etl_runs ADD COLUMN IF NOT EXISTS execution text",
    "ALTER TABLE etl_runs ADD COLUMN IF NOT EXISTS task_index integer",
    # The sweeper's scan: running rows by heartbeat.
    """
    CREATE INDEX IF NOT EXISTS etl_runs_running_heartbeat
        ON etl_runs (heartbeat_at) WHERE status = 'running'
    """,
    "CREATE INDEX IF NOT EXISTS etl_runs_org_created ON etl_runs (org_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS etl_steps (
        step_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        job_id uuid NOT NULL,
        ordinal integer NOT NULL,
        name text NOT NULL,
        status text NOT NULL,
        started_at timestamp with time zone NOT NULL DEFAULT now(),
        finished_at timestamp with time zone,
        records_in bigint,
        records_out bigint,
        detail jsonb,
        error_type text,
        error_message text
    )
    """,
    "CREATE INDEX IF NOT EXISTS etl_steps_job ON etl_steps (job_id, ordinal)",
    """
    CREATE TABLE IF NOT EXISTS etl_watermarks (
        org_id text NOT NULL,
        connector_key text NOT NULL,
        entity text NOT NULL,
        last_high_watermark timestamp with time zone,
        last_run_at timestamp with time zone,
        mode text
    )
    """,
    # ON CONFLICT target of the ledger's upsert.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS etl_watermarks_key
        ON etl_watermarks (org_id, connector_key, entity)
    """,
)

_DOWN = (
    "DROP INDEX IF EXISTS etl_watermarks_key",
    "DROP INDEX IF EXISTS etl_steps_job",
    "DROP TABLE IF EXISTS etl_steps",
    "DROP INDEX IF EXISTS etl_runs_org_created",
    "DROP INDEX IF EXISTS etl_runs_running_heartbeat",
    "ALTER TABLE etl_runs DROP COLUMN IF EXISTS task_index",
    "ALTER TABLE etl_runs DROP COLUMN IF EXISTS execution",
    "ALTER TABLE etl_runs DROP COLUMN IF EXISTS finished_at",
    "ALTER TABLE etl_runs DROP COLUMN IF EXISTS heartbeat_at",
    "ALTER TABLE etl_runs DROP COLUMN IF EXISTS pipeline",
)


def upgrade() -> None:
    for statement in _UP:
        op.execute(statement)


def downgrade() -> None:
    """Drops only what this revision added; legacy's tables and columns stay."""
    for statement in _DOWN:
        op.execute(statement)
