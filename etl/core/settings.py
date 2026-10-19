"""Every setting of the ETL. The only module that reads environment variables.

Names are nested with a double underscore (`POSTGRES__HOST`, `CLICKHOUSE__DATABASE`), as in
clevrr-backend-2.0. One name per setting: legacy's aliases (`CKH_*`, `CLICKHOUSE_*` mixes)
are gone. No secret has a default.

Write targets: the ETL writes only `postgres.target_schema` and `clickhouse.database`. While
v2 is proven against legacy they are `etl_v2` and `clevrr_db_v2` on sandbox (decision
2026-10-17), so v2 never touches legacy's tables.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z_][a-z0-9_]{0,62}$")]
# A cluster name only ever appears quoted (ON CLUSTER '<name>'), and the sandbox's has a hyphen.
ClusterName = Annotated[str, StringConstraints(pattern=r"^[a-z_][a-z0-9_-]{0,62}$")]


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PostgresSettings(_Section):
    host: str
    port: int = 5432
    db: str
    user: str
    password: SecretStr
    # Every ETL connection's search_path is this schema alone: unqualified names never reach
    # another schema. Legacy-owned tables are read with an explicit `public.` prefix.
    target_schema: Identifier = "public"
    # Legacy set this on every connection (utils/db.py). The sales transforms convert with
    # AT TIME ZONE 'Asia/Kolkata' and store into timestamp-without-time-zone columns, so the
    # session zone decides the stored wall-clock value; UTC stored every order 5.5 h early.
    session_timezone: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_]+(/[A-Za-z_]+)*$")] = (
        "Asia/Kolkata"
    )
    pool_max: int = Field(default=4, ge=1, le=32)
    connect_timeout_s: int = Field(default=10, ge=1, le=120)


class ClickHouseSettings(_Section):
    host: str
    port: int = 8123
    user: str
    password: SecretStr
    database: Identifier = "clevrr_db"
    secure: bool = False
    # Set on a multi-node cluster: mutations and DDL then run ON CLUSTER '<cluster>'.
    cluster: ClusterName | None = None
    connect_timeout_s: int = Field(default=10, ge=1, le=120)
    query_timeout_s: int = Field(default=900, ge=10, le=7200)
    insert_batch_rows: int = Field(default=50_000, ge=1_000, le=1_000_000)


class RunSettings(_Section):
    heartbeat_s: int = Field(default=30, ge=5, le=300)
    # A `running` run whose heartbeat is older than this is marked `killed` by the sweeper.
    stale_after_s: int = Field(default=600, ge=60, le=86_400)
    # Non-empty: only these orgs run, whatever --org says. Sandbox parity runs set the pilot
    # orgs here (live provider calls are allowed for them only, decision 2026-10-17).
    org_allowlist: list[str] = []


class AlertSettings(_Section):
    slack_webhook_url: SecretStr | None = None


class ApiSettings(_Section):
    """The backend's internal API (ADR-0003, later). Unset: the ETL makes no API calls."""

    base_url: str | None = None
    # The OIDC audience the backend expects (its require_internal setting).
    audience: str | None = None
    timeout_s: float = Field(default=20.0, gt=0, le=120)


class GoogleAdsSettings(_Section):
    """The OAuth client that refreshes a Google Ads connector's token (legacy
    GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET / GOOGLE_ADS_DEVELOPER_TOKEN). The new
    token stays in memory: legacy saved it back to organization_connectors, v2 never does."""

    client_id: SecretStr | None = None
    client_secret: SecretStr | None = None
    developer_token: SecretStr | None = None  # used when the connector has none


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__", env_file=".env", extra="ignore", frozen=True
    )

    env: str = "local"
    log_level: str = "INFO"
    postgres: PostgresSettings
    clickhouse: ClickHouseSettings
    run: RunSettings = RunSettings()
    alerts: AlertSettings = AlertSettings()
    api: ApiSettings = ApiSettings()
    google_ads: GoogleAdsSettings = GoogleAdsSettings()
    # Cloud Run Jobs set these on every task; one job = N tasks = N org shards.
    task_index: int = Field(default=0, ge=0, validation_alias="CLOUD_RUN_TASK_INDEX")
    task_count: int = Field(default=1, ge=1, validation_alias="CLOUD_RUN_TASK_COUNT")
    execution: str | None = Field(default=None, validation_alias="CLOUD_RUN_EXECUTION")


def load_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue] - pydantic-settings fills it from the environment
