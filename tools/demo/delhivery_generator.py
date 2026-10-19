"""
Delhivery Data Generator
Generates realistic shipment tracking data (shipment_scans table).
"""

import random
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from .config import (
    DEMO_ORG_ID, DEMO_DELHIVERY_SOURCE_TYPE,
    DELHIVERY_STATUS_FLOW, DELHIVERY_RTO_FLOW, DELHIVERY_HUBS,
    DELIVERY_SUCCESS_RATE, RTO_RATE, IN_TRANSIT_RATE,
    INDIAN_CITIES,
)


class DelhiveryDataGenerator:
    """Generates realistic Delhivery shipment tracking data."""

    def __init__(self, org_id: str = DEMO_ORG_ID, seed: int = None):
        self.org_id = org_id
        self.source_type = DEMO_DELHIVERY_SOURCE_TYPE

        if seed:
            random.seed(seed)

        # Hub mapping for cities
        self._hub_map = {hub["city"]: hub["hub"] for hub in DELHIVERY_HUBS}
        self._default_hub = "DEL/DL/DEL"

    def _generate_waybill(self, order_id: int) -> str:
        """Generate a realistic Delhivery waybill number."""
        # Delhivery waybills are typically 10-14 digit numbers
        prefix = random.choice(["SF", "CC", "PP", ""])
        number = f"{random.randint(1000000000, 9999999999)}"
        return f"{prefix}{number}"

    def _get_hub_for_city(self, city: str) -> str:
        """Get the Delhivery hub code for a city."""
        return self._hub_map.get(city, self._default_hub)

    def _determine_shipment_outcome(self, current_date: datetime, order_date: datetime) -> str:
        """
        Determine shipment outcome based on time since order.
        Recent orders may still be in transit.
        """
        days_since_order = (current_date - order_date).days

        # If order is very recent (< 3 days), most are still in transit
        if days_since_order < 3:
            return "in_transit"

        # If order is 3-7 days old, mix of delivered and in transit
        if days_since_order < 7:
            rand = random.random()
            if rand < 0.6:
                return "delivered"
            elif rand < 0.9:
                return "in_transit"
            else:
                return "rto"

        # Older orders - apply standard distribution
        rand = random.random()
        if rand < DELIVERY_SUCCESS_RATE:
            return "delivered"
        elif rand < DELIVERY_SUCCESS_RATE + RTO_RATE:
            return "rto"
        else:
            return "in_transit"

    def _generate_scan_time(self, base_date: datetime, hour_range: tuple = (8, 20)) -> datetime:
        """Generate a realistic scan time during business hours."""
        hour = random.randint(hour_range[0], hour_range[1])
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        return base_date.replace(hour=hour, minute=minute, second=second)

    def generate_shipment_scans(
        self,
        order_id: int,
        order_date: datetime,
        city: str,
        current_date: datetime,
        shipping_carrier: Optional[str] = None,
        *,
        waybill: Optional[str] = None,
        outcome: Optional[str] = None,
        origin_hub: Optional[str] = None,
        destination_hub: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Generate shipment scan records for a single order.

        Args:
            order_id: The Shopify order ID
            order_date: When the order was placed
            city: Destination city
            current_date: Current date (for determining delivery status)
            shipping_carrier: Optional carrier override

        Returns:
            List of shipment_scans records
        """
        # Only generate for orders that have shipping
        if shipping_carrier and shipping_carrier.lower() != "delhivery":
            return []

        waybill = waybill or self._generate_waybill(order_id)
        destination_hub = destination_hub or self._get_hub_for_city(city)
        origin_hub = origin_hub or random.choice(DELHIVERY_HUBS)["hub"]

        # Determine outcome
        outcome = outcome or self._determine_shipment_outcome(current_date, order_date)

        # Select appropriate status flow
        if outcome == "rto":
            status_flow = DELHIVERY_RTO_FLOW
        else:
            status_flow = DELHIVERY_STATUS_FLOW

        scans = []
        scan_date = order_date

        for i, status in enumerate(status_flow):
            # Calculate scan date
            scan_date = order_date + timedelta(days=status["day_offset"])

            # Don't generate future scans
            if scan_date > current_date:
                break

            # For in_transit outcome, stop before delivery
            if outcome == "in_transit" and status["code"] in ["DL", "OFD"]:
                break

            scan_time = self._generate_scan_time(scan_date)

            # Determine location
            if status["code"] in ["UD", "PU"]:
                location = origin_hub
            elif status["code"] in ["RIT", "RD"]:
                location = origin_hub
            elif status["code"] in ["IT"]:
                location = f"{origin_hub} -> {destination_hub}"
            else:
                location = destination_hub

            # Build activity description
            activity_desc = status["description"]
            if status["code"] == "DL":
                activity_desc = f"Delivered to {city} - Received by: Customer"
            elif status["code"] == "DA":
                reasons = [
                    "Customer not available",
                    "Address not found",
                    "Customer refused delivery",
                    "Delivery rescheduled by customer",
                ]
                activity_desc = f"Delivery attempted - {random.choice(reasons)}"

            scan = {
                "organization_id": self.org_id,
                "waybill": waybill,
                "source_type": self.source_type,
                "scan_datetime": scan_time,
                "scan_status": status["status"],
                "status_code": status["code"],
                "scanned_location": location,
                "instructions": None,
                "activity_description": activity_desc,
                "created_at": datetime.utcnow(),
            }
            scans.append(scan)

        return scans

    def generate_shipments_and_scans_for_orders(
        self,
        orders: List[Dict[str, Any]],
        current_date: datetime
    ) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
        """
        Generate shipments and shipment scans for orders (Delhivery only).
        Returns (shipments, shipment_scans)
        """
        shipments: list[Dict[str, Any]] = []
        all_scans: list[Dict[str, Any]] = []

        for order in orders:
            if order.get("fulfillment_status") not in ["fulfilled", "partial"]:
                continue

            carrier = order.get("shipping_carrier", "Delhivery")
            if carrier and carrier.lower() not in ["delhivery", ""]:
                if random.random() > 0.6:
                    continue

            waybill = self._generate_waybill(order["order_id"])
            destination_hub = self._get_hub_for_city(order["city"])
            origin_hub = random.choice(DELHIVERY_HUBS)["hub"]
            outcome = self._determine_shipment_outcome(current_date, order["order_date"])

            # Basic shipment record
            current_status = {
                "delivered": "Delivered",
                "rto": "RTO",
                "in_transit": "In Transit"
            }.get(outcome, "In Transit")

            status_code = {
                "delivered": "DL",
                "rto": "RD",
                "in_transit": "IT"
            }.get(outcome, "IT")

            pickup_date = order["order_date"]
            expected_delivery = pickup_date + timedelta(days=random.randint(3, 7))
            delivery_date = expected_delivery if outcome == "delivered" else None

            shipment = {
                "organization_id": self.org_id,
                "waybill": waybill,
                "order_id": str(order["order_id"]),
                "source_type": self.source_type,
                "aggregator": "delhivery",
                "reference_no": str(order["order_id"]),
                "pickup_location": origin_hub,
                "pickup_date": pickup_date,
                "expected_delivery_date": expected_delivery,
                "delivery_date": delivery_date,
                "current_status": current_status,
                "status_code": status_code,
                "consignee_name": order.get("customer_name"),
                "consignee_city": order.get("city"),
                "consignee_state": order.get("state"),
                "consignee_pincode": order.get("zip"),
                "sender_name": "Delhivery",
                "origin": origin_hub,
                "destination": destination_hub,
                "invoice_amount": order.get("total_price"),
                "cod_amount": 0.0,
                "weight": round(random.uniform(0.5, 3.0), 2),
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
            shipments.append(shipment)

            scans = self.generate_shipment_scans(
                order_id=order["order_id"],
                order_date=order["order_date"],
                city=order["city"],
                current_date=current_date,
                shipping_carrier=carrier,
                waybill=waybill,
                outcome=outcome,
                origin_hub=origin_hub,
                destination_hub=destination_hub,
            )
            all_scans.extend(scans)

        return shipments, all_scans

    def get_delivery_stats(self, scans: List[Dict]) -> Dict[str, Any]:
        """Get summary statistics for generated scans (useful for debugging)."""
        waybills = set(scan["waybill"] for scan in scans)
        delivered = set()
        rto = set()
        in_transit = set()

        for scan in scans:
            wb = scan["waybill"]
            if scan["status_code"] == "DL":
                delivered.add(wb)
            elif scan["status_code"] == "RD":
                rto.add(wb)

        in_transit = waybills - delivered - rto

        return {
            "total_shipments": len(waybills),
            "delivered": len(delivered),
            "rto": len(rto),
            "in_transit": len(in_transit),
            "delivery_rate": round(len(delivered) / len(waybills) * 100, 1) if waybills else 0,
            "rto_rate": round(len(rto) / len(waybills) * 100, 1) if waybills else 0,
        }
