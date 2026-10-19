"""Open a Shopify client for one org: the shop url and token from its connector, and the API
version from `public.connectors` (legacy read the same row through the app API)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Final

import httpx

from etl.core.model import Org, RunContext, StepFailed
from etl.sources.shopify.client import Shop, ShopifyClient, access_token, shop_url

MAX_RPM = 40  # legacy's connector_endpoints row for products; Shopify's REST bucket allows 2/s
_API_VERSION: Final = "SELECT api_version FROM public.connectors WHERE key = 'shopify'"


def _http() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))


def api_version(ctx: RunContext) -> str:
    with ctx.pg.connection() as conn:
        found = conn.execute(_API_VERSION).fetchone()
    if not found or not found[0]:
        raise StepFailed("public.connectors has no Shopify api_version.")
    return str(found[0])


@contextmanager
def connect(ctx: RunContext, org: Org) -> Generator[ShopifyClient]:
    with _http() as http:
        token = access_token(org.credentials, http)
        shop = Shop(shop_url(org.credentials), api_version(ctx), token)
        yield ShopifyClient(shop, http, max_rpm=MAX_RPM)
