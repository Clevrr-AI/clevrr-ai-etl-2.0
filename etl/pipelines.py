"""The declarative list of pipelines: name -> steps, windows, schedule hint, timeout, retries.

Each source package defines its pipelines; this file lists them as they are ported (plan 7.3).
Amazon Ads is not ported (decision 2026-10-17).
"""

from __future__ import annotations

from etl.checks.reliability import RELIABILITY_GOOGLE, RELIABILITY_META, RELIABILITY_SHOPIFY
from etl.core.model import Pipeline
from etl.sources.costs.mirror import COSTS
from etl.sources.google_ads.pipeline import GOOGLE_ADS
from etl.sources.meta.pipeline import META_ADS, META_ADS_REGION
from etl.sources.shopify.catalog import SHOPIFY_CATALOG
from etl.sources.shopify.sales import SHOPIFY_SALES

PIPELINES: dict[str, Pipeline] = {}


def register(pipeline: Pipeline) -> Pipeline:
    if pipeline.name in PIPELINES:
        raise ValueError(f"Pipeline {pipeline.name} is registered twice.")
    PIPELINES[pipeline.name] = pipeline
    return pipeline


def get(name: str) -> Pipeline:
    try:
        return PIPELINES[name]
    except KeyError:
        known = ", ".join(sorted(PIPELINES)) or "none yet"
        raise SystemExit(f"Unknown pipeline {name!r}. Known: {known}.") from None


for _pipeline in (
    SHOPIFY_CATALOG,
    SHOPIFY_SALES,
    META_ADS,
    META_ADS_REGION,
    GOOGLE_ADS,
    COSTS,
    RELIABILITY_SHOPIFY,
    RELIABILITY_META,
    RELIABILITY_GOOGLE,
):
    register(_pipeline)
