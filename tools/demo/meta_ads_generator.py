"""
Meta Ads Data Generator
Generates realistic Meta (Facebook/Instagram) advertising data for ClickHouse advertising table.
"""

import random
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any

from .config import (
    DEMO_ORG_ID, DEMO_META_SOURCE_TYPE,
    META_AD_CAMPAIGNS, META_AD_CREATIVES, META_ADS_BENCHMARKS,
    DAY_OF_WEEK_MULTIPLIERS,
)


class MetaAdsDataGenerator:
    """Generates realistic Meta Ads performance data."""

    def __init__(self, org_id: str = DEMO_ORG_ID, seed: int = None):
        self.org_id = org_id
        self.source_type = DEMO_META_SOURCE_TYPE
        self.account_id = "act_123456789012"
        self.account_name = "ClevrrStyle Ads Account"

        if seed:
            random.seed(seed)

        # Pre-generate ad structure
        self.ad_structure = self._build_ad_structure()

    def _build_ad_structure(self) -> List[Dict]:
        """Build complete ad hierarchy: campaigns -> ad_sets -> ads."""
        ads = []
        ad_counter = 1

        for campaign in META_AD_CAMPAIGNS:
            for ad_set in campaign["ad_sets"]:
                # Each ad set has 2-4 creatives
                num_creatives = random.randint(2, 4)
                selected_creatives = random.sample(
                    META_AD_CREATIVES,
                    min(num_creatives, len(META_AD_CREATIVES))
                )

                for creative in selected_creatives:
                    ad_id = f"ad_{ad_counter:06d}"
                    ad_counter += 1

                    ad = {
                        "ad_id": ad_id,
                        "ad_name": f"{creative['name']} - {ad_set['name'][:20]}",
                        "ad_group_id": ad_set["id"],
                        "ad_group_name": ad_set["name"],
                        "campaign_id": campaign["campaign_id"],
                        "campaign_name": campaign["campaign_name"],
                        "campaign_status": campaign["status"],
                        "ad_status": "ACTIVE" if random.random() > 0.1 else "PAUSED",
                        "ad_group_status": "ACTIVE" if random.random() > 0.15 else "PAUSED",
                        "objective": campaign["objective"],
                        "targeting": ad_set["targeting"],
                        "creative_type": creative["type"],
                        "ctr_modifier": creative["ctr_modifier"],
                        "daily_budget_range": campaign["daily_budget_range"],
                    }
                    ads.append(ad)

        return ads

    def _get_base_metrics(self, ad: Dict, date: datetime) -> Dict[str, float]:
        """
        Generate base performance metrics for an ad on a given date.
        Takes into account ad type, targeting, and day of week.
        """
        benchmarks = META_ADS_BENCHMARKS

        # Day of week modifier
        day_modifier = DAY_OF_WEEK_MULTIPLIERS[date.weekday()]

        # Targeting modifier (retargeting performs better)
        targeting_modifier = {
            "lookalike": 1.1,
            "retargeting": 1.3,
            "dpa": 1.4,
            "interest": 1.0,
            "broad": 0.85,
        }.get(ad["targeting"], 1.0)

        # Budget allocation (distribute daily budget across ads)
        budget_min, budget_max = ad["daily_budget_range"]
        total_budget = random.uniform(budget_min, budget_max) * day_modifier

        # Number of ads in this campaign's ad sets
        ads_in_campaign = len([a for a in self.ad_structure if a["campaign_id"] == ad["campaign_id"]])
        ad_budget = total_budget / max(ads_in_campaign, 1)

        # Skip if ad is paused (spend = 0)
        if ad["ad_status"] != "ACTIVE" or ad["ad_group_status"] != "ACTIVE":
            return {
                "spend": 0,
                "impressions": 0,
                "clicks": 0,
                "landing_page_views": 0,
                "add_to_cart": 0,
                "checkout_initiated": 0,
                "add_payment_info": 0,
                "purchases": 0,
                "revenue": 0,
            }

        # CPM determines impressions
        cpm = random.uniform(*benchmarks["cpm_range"]) / targeting_modifier
        impressions = int((ad_budget / cpm) * 1000)

        # CTR determines clicks
        base_ctr = random.uniform(*benchmarks["ctr_range"]) / 100
        ctr = base_ctr * ad["ctr_modifier"] * targeting_modifier
        clicks = int(impressions * ctr)

        # Funnel metrics
        lpv_rate = random.uniform(*benchmarks["landing_page_view_rate"])
        landing_page_views = int(clicks * lpv_rate)

        atc_rate = random.uniform(*benchmarks["add_to_cart_rate"]) * targeting_modifier
        add_to_cart = int(landing_page_views * atc_rate)

        ci_rate = random.uniform(*benchmarks["checkout_initiated_rate"])
        checkout_initiated = int(add_to_cart * ci_rate)

        # Add payment info (slightly less than checkout)
        add_payment_info = int(checkout_initiated * random.uniform(0.85, 0.95))

        # Purchases
        purchase_rate = random.uniform(*benchmarks["purchase_rate"])
        purchases = int(checkout_initiated * purchase_rate)

        # Revenue (AOV varies by targeting)
        aov_base = random.uniform(1800, 3200)
        aov_modifier = {"retargeting": 1.15, "dpa": 1.2, "lookalike": 1.05}.get(ad["targeting"], 1.0)
        revenue = purchases * aov_base * aov_modifier

        # Actual spend (may vary slightly from budget)
        spend = ad_budget * random.uniform(0.85, 1.0)

        return {
            "spend": round(spend, 2),
            "impressions": impressions,
            "clicks": clicks,
            "landing_page_views": landing_page_views,
            "add_to_cart": add_to_cart,
            "checkout_initiated": checkout_initiated,
            "add_payment_info": add_payment_info,
            "purchases": purchases,
            "revenue": round(revenue, 2),
        }

    def generate_ads_for_date(self, date: datetime) -> List[Dict]:
        """
        Generate advertising data for all ads on a specific date.

        Returns:
            List of advertising records for ClickHouse
        """
        records = []

        for ad in self.ad_structure:
            metrics = self._get_base_metrics(ad, date)

            # Skip if no impressions
            if metrics["impressions"] == 0:
                continue

            # Add some daily variation
            variation = random.uniform(0.8, 1.2)
            for key in ["impressions", "clicks", "landing_page_views", "add_to_cart",
                        "checkout_initiated", "add_payment_info", "purchases"]:
                metrics[key] = int(metrics[key] * variation)
            metrics["spend"] = round(metrics["spend"] * variation, 2)
            metrics["revenue"] = round(metrics["revenue"] * variation, 2)

            record = {
                "id": str(uuid.uuid4()),
                "organization_id": self.org_id,
                "source_type": self.source_type,
                "ad_id": ad["ad_id"],
                "ad_name": ad["ad_name"],
                "ad_group_id": ad["ad_group_id"],
                "ad_group_name": ad["ad_group_name"],
                "ad_status": ad["ad_status"],
                "ad_group_status": ad["ad_group_status"],
                "campaign_id": ad["campaign_id"],
                "campaign_name": ad["campaign_name"],
                "campaign_status": ad["campaign_status"],
                "account_id": self.account_id,
                "account_name": self.account_name,
                "impressions": metrics["impressions"],
                "clicks": metrics["clicks"],
                "landing_page_view": metrics["landing_page_views"],
                "add_to_cart": metrics["add_to_cart"],
                "checkout_initatied": metrics["checkout_initiated"],  # Note: typo matches schema
                "add_payment_info": metrics["add_payment_info"],
                "number_of_purchases": metrics["purchases"],
                "spend": Decimal(str(metrics["spend"])),
                "revenue": Decimal(str(metrics["revenue"])),
                "created_at": date.replace(hour=23, minute=59, second=59),
                "last_synced": datetime.utcnow(),
            }
            records.append(record)

        return records

    def get_summary_metrics(self, date: datetime) -> Dict[str, Any]:
        """Get aggregated metrics for a date (useful for debugging/logging)."""
        records = self.generate_ads_for_date(date)

        total_spend = sum(float(r["spend"]) for r in records)
        total_revenue = sum(float(r["revenue"]) for r in records)
        total_impressions = sum(r["impressions"] for r in records)
        total_clicks = sum(r["clicks"] for r in records)
        total_purchases = sum(r["number_of_purchases"] for r in records)

        return {
            "date": date.strftime("%Y-%m-%d"),
            "total_ads": len(records),
            "total_spend": round(total_spend, 2),
            "total_revenue": round(total_revenue, 2),
            "total_impressions": total_impressions,
            "total_clicks": total_clicks,
            "total_purchases": total_purchases,
            "roas": round(total_revenue / total_spend, 2) if total_spend > 0 else 0,
            "ctr": round((total_clicks / total_impressions) * 100, 2) if total_impressions > 0 else 0,
        }
