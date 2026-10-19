"""
Shopify Data Generator
Generates realistic Shopify data: products, product_variants, orders, order_lines, refunds, refund_lines
"""

import random
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Tuple, Optional

from .config import (
    DEMO_ORG_ID, DEMO_SOURCE_TYPE,
    PRODUCT_CATALOG, INDIAN_CITIES, get_total_city_weight,
    ORDER_HOUR_WEIGHTS, DAY_OF_WEEK_MULTIPLIERS,
    FINANCIAL_STATUS_DISTRIBUTION, FULFILLMENT_STATUS_DISTRIBUTION,
    SALE_CATEGORY_DISTRIBUTION, DISCOUNT_CODES, ITEMS_PER_ORDER_DISTRIBUTION,
    FIRST_NAMES, LAST_NAMES, DAILY_ORDER_MIN, DAILY_ORDER_MAX,
)


class ShopifyDataGenerator:
    """Generates realistic Shopify e-commerce data."""

    def __init__(self, org_id: str = DEMO_ORG_ID, seed: int = None):
        self.org_id = org_id
        self.source_type = DEMO_SOURCE_TYPE
        if seed:
            random.seed(seed)

        # ID counters (start high to avoid conflicts)
        self._product_id_counter = 1000000
        self._variant_id_counter = 2000000
        self._order_id_counter = 3000000
        self._order_item_id_counter = 4000000
        self._refund_id_counter = 5000000
        self._refund_line_id_counter = 6000000
        self._customer_id_counter = 7000000

        # Pre-generate products and variants
        self.products: List[Dict] = []
        self.variants: List[Dict] = []
        self._generate_product_catalog()

        # City weights for selection
        self._city_weights = [city["weight"] for city in INDIAN_CITIES]
        self._total_city_weight = get_total_city_weight()

    def _next_product_id(self) -> int:
        self._product_id_counter += 1
        return self._product_id_counter

    def _next_variant_id(self) -> int:
        self._variant_id_counter += 1
        return self._variant_id_counter

    def _next_order_id(self) -> int:
        self._order_id_counter += 1
        return self._order_id_counter

    def _next_order_item_id(self) -> int:
        self._order_item_id_counter += 1
        return self._order_item_id_counter

    def _next_refund_id(self) -> int:
        self._refund_id_counter += 1
        return self._refund_id_counter

    def _next_refund_line_id(self) -> int:
        self._refund_line_id_counter += 1
        return self._refund_line_id_counter

    def _next_customer_id(self) -> int:
        self._customer_id_counter += 1
        return self._customer_id_counter

    def _generate_product_catalog(self):
        """Generate all products and variants from config catalog."""
        for catalog_item in PRODUCT_CATALOG:
            product_id = self._next_product_id()
            handle = catalog_item["name"].lower().replace(" ", "-").replace("'", "")

            # Create product
            product = {
                "product_id": product_id,
                "organization_id": self.org_id,
                "title": catalog_item["name"],
                "handle": handle,
                "status": "active",
                "vendor": "ClevrrStyle",
                "product_type": catalog_item["category"],
                "tags": f"{catalog_item['category']},fashion,trending",
                "created_at": datetime(2025, 11, 1) + timedelta(days=random.randint(0, 30)),
                "updated_at": datetime(2025, 12, 1) + timedelta(days=random.randint(0, 15)),
                "image_url": f"https://cdn.clevrrstore.com/products/{handle}.jpg",
                "variants_count": len(catalog_item["variants"]) * len(catalog_item["colors"]),
                "has_variants": True,
                "options_count": 2 if len(catalog_item["colors"]) > 1 else 1,
                "handling_cost": round(random.uniform(20, 50), 2),
                "default_price": Decimal(str(catalog_item["base_price"])),
                "default_variant_id": None,  # Will be set after variants generated
                "sku_id": f"CLV-{product_id}",
                "cost_per_item": Decimal(str(catalog_item["cost"])),
                "collection_name": catalog_item["category"],
            }

            # Create variants
            product_variants = []
            position = 0
            for size in catalog_item["variants"]:
                for color in catalog_item["colors"]:
                    position += 1
                    variant_id = self._next_variant_id()

                    # Price variation based on size
                    size_modifier = 1.0
                    if size in ["XL", "XXL", "36", "38"]:
                        size_modifier = 1.05
                    elif size in ["XS", "26", "28"]:
                        size_modifier = 0.95

                    price = round(catalog_item["base_price"] * size_modifier, 2)
                    compare_price = round(price * random.uniform(1.1, 1.3), 2)

                    variant = {
                        "variant_id": variant_id,
                        "organization_id": self.org_id,
                        "product_id": product_id,
                        "title": f"{size} / {color}",
                        "sku": f"CLV-{product_id}-{size}-{color[:3].upper()}",
                        "position": position,
                        "price": Decimal(str(price)),
                        "compare_at_price": Decimal(str(compare_price)),
                        "option1": size,
                        "option2": color,
                        "option3": None,
                        "image_url": f"https://cdn.clevrrstore.com/variants/{handle}-{color.lower().replace(' ', '-')}.jpg",
                        "inventory_quantity": random.randint(10, 200),
                        "inventory_item_id": variant_id + 10000000,
                        "requires_shipping": True,
                        "taxable": True,
                        "created_at": product["created_at"],
                        "updated_at": product["updated_at"],
                        "cost_per_item": Decimal(str(catalog_item["cost"])),
                        "shopify_cost_per_item": Decimal(str(catalog_item["cost"])),
                        "source_type": self.source_type,
                        "is_default": position == 1,
                    }
                    product_variants.append(variant)

            # Set default variant
            if product_variants:
                product["default_variant_id"] = product_variants[0]["variant_id"]

            self.products.append(product)
            self.variants.extend(product_variants)

    def _select_city(self) -> Dict:
        """Select a city based on weighted distribution."""
        return random.choices(INDIAN_CITIES, weights=self._city_weights, k=1)[0]

    def _select_financial_status(self) -> str:
        """Select financial status based on distribution."""
        statuses = list(FINANCIAL_STATUS_DISTRIBUTION.keys())
        weights = list(FINANCIAL_STATUS_DISTRIBUTION.values())
        return random.choices(statuses, weights=weights, k=1)[0]

    def _select_fulfillment_status(self, financial_status: str) -> Optional[str]:
        """Select fulfillment status based on financial status."""
        if financial_status in ["voided", "pending"]:
            return None
        statuses = list(FULFILLMENT_STATUS_DISTRIBUTION.keys())
        weights = list(FULFILLMENT_STATUS_DISTRIBUTION.values())
        return random.choices(statuses, weights=weights, k=1)[0]

    def _select_sale_category(self) -> str:
        """Select sale initiated category based on distribution."""
        categories = list(SALE_CATEGORY_DISTRIBUTION.keys())
        weights = list(SALE_CATEGORY_DISTRIBUTION.values())
        return random.choices(categories, weights=weights, k=1)[0]

    def _select_discount(self) -> Tuple[Optional[str], float]:
        """Select discount code and percentage."""
        codes = DISCOUNT_CODES
        weights = [d["usage_weight"] for d in codes]
        selected = random.choices(codes, weights=weights, k=1)[0]
        return selected["code"], selected["discount_percent"] / 100

    def _select_items_count(self) -> int:
        """Select number of items in order."""
        counts = list(ITEMS_PER_ORDER_DISTRIBUTION.keys())
        weights = list(ITEMS_PER_ORDER_DISTRIBUTION.values())
        return random.choices(counts, weights=weights, k=1)[0]

    def _generate_customer_name(self) -> str:
        """Generate a realistic Indian name."""
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        return f"{first} {last}"

    def _generate_phone(self) -> str:
        """Generate a realistic Indian phone number."""
        prefix = random.choice(["91", "+91"])
        first_digit = random.choice(["6", "7", "8", "9"])
        remaining = "".join([str(random.randint(0, 9)) for _ in range(9)])
        return f"{prefix}{first_digit}{remaining}"

    def _generate_email(self, name: str) -> str:
        """Generate email from name."""
        domains = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "rediffmail.com"]
        clean_name = name.lower().replace(" ", random.choice([".", "_", ""]))
        suffix = random.randint(1, 999) if random.random() > 0.5 else ""
        return f"{clean_name}{suffix}@{random.choice(domains)}"

    def _generate_order_time(self, date: datetime) -> datetime:
        """Generate order time with realistic hour distribution."""
        hours = list(ORDER_HOUR_WEIGHTS.keys())
        weights = list(ORDER_HOUR_WEIGHTS.values())
        hour = random.choices(hours, weights=weights, k=1)[0]
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        return date.replace(hour=hour, minute=minute, second=second)

    def _get_daily_order_count(self, date: datetime) -> int:
        """Get number of orders for a specific date considering day-of-week."""
        base_count = random.randint(DAILY_ORDER_MIN, DAILY_ORDER_MAX)
        day_multiplier = DAY_OF_WEEK_MULTIPLIERS[date.weekday()]

        # Add some randomness
        variation = random.uniform(0.9, 1.1)

        # Special boost for certain dates (festivals, sales)
        special_boost = 1.0
        # Christmas/New Year period
        if date.month == 12 and date.day >= 20:
            special_boost = 1.3
        # New Year
        if date.month == 1 and date.day <= 5:
            special_boost = 1.25

        return int(base_count * day_multiplier * variation * special_boost)

    def generate_orders_for_date(self, date: datetime) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
        """
        Generate all orders, order_lines, refunds, and refund_lines for a specific date.

        Returns:
            Tuple of (orders, order_lines, refunds, refund_lines)
        """
        orders = []
        order_lines = []
        refunds = []
        refund_lines = []

        order_count = self._get_daily_order_count(date)

        for _ in range(order_count):
            order_data = self._generate_single_order(date)
            orders.append(order_data["order"])
            order_lines.extend(order_data["order_lines"])

            if order_data["refund"]:
                refunds.append(order_data["refund"])
                refund_lines.extend(order_data["refund_lines"])

        return orders, order_lines, refunds, refund_lines

    def _generate_single_order(self, date: datetime) -> Dict[str, Any]:
        """Generate a single complete order with all related data."""
        order_id = self._next_order_id()
        customer_id = self._next_customer_id()
        order_time = self._generate_order_time(date)

        # Select attributes
        city_data = self._select_city()
        financial_status = self._select_financial_status()
        fulfillment_status = self._select_fulfillment_status(financial_status)
        sale_category = self._select_sale_category()
        discount_code, discount_rate = self._select_discount()
        items_count = self._select_items_count()

        # Customer info
        customer_name = self._generate_customer_name()
        phone = self._generate_phone()
        email = self._generate_email(customer_name)

        # Select random variants for this order
        selected_variants = random.sample(
            self.variants,
            min(items_count, len(self.variants))
        )

        # Generate order lines
        order_lines_data = []
        subtotal = Decimal("0")
        total_tax = Decimal("0")

        for variant in selected_variants:
            quantity = random.choices([1, 2, 3], weights=[0.7, 0.25, 0.05], k=1)[0]
            base_price = Decimal(str(variant["price"]))
            item_price = (base_price * Decimal(quantity)).quantize(Decimal("0.01"))
            tax_rate = Decimal("0.18")  # 18% GST
            tax_amount = (item_price * tax_rate).quantize(Decimal("0.01"))

            # Apply discount
            discount_amount = (item_price * Decimal(str(discount_rate))).quantize(Decimal("0.01"))

            order_item_id = self._next_order_item_id()

            # Find product for this variant
            product = next((p for p in self.products if p["product_id"] == variant["product_id"]), None)

            order_line = {
                "order_item_id": order_item_id,
                "order_id": order_id,
                "source_type": self.source_type,
                "variant_id": variant["variant_id"],
                "quantity": quantity,
                "item_price": item_price,
                "discount_allocated": discount_amount,
                "tax_allocated": tax_amount,
                "product_id": str(variant["product_id"]),
                "product_name": product["title"] if product else "Unknown Product",
                "last_synced": datetime.utcnow(),
                "created_at": order_time,
                "updated_at": order_time,
                "city": city_data["city"],
                "organization_id": self.org_id,
                "sale_initiated_category": sale_category,
            }
            order_lines_data.append(order_line)

            subtotal += item_price
            total_tax += tax_amount

        # Calculate totals
        total_discounts = (subtotal * Decimal(str(discount_rate))).quantize(Decimal("0.01"))
        shipping_price = Decimal("0") if subtotal > Decimal("999") else Decimal("99")
        total_price = subtotal - total_discounts + total_tax + shipping_price

        # Generate ZIP code
        zip_code = f"{city_data['pin_prefix']}{random.randint(100, 999):03d}"

        # Cancelled at (for voided orders)
        cancelled_at = None
        if financial_status == "voided":
            cancelled_at = order_time + timedelta(hours=random.randint(1, 24))

        # Shipping carrier (for fulfilled orders)
        shipping_carrier = None
        shipment_status = None
        if fulfillment_status == "fulfilled":
            shipping_carrier = random.choice(["Delhivery", "BlueDart", "DTDC", "Ecom Express"])
            shipment_status = "delivered"

        order = {
            "organization_id": self.org_id,
            "order_id": order_id,
            "order_date": order_time,
            "customer_id": customer_id,
            "subtotal_price": subtotal,
            "total_discounts": total_discounts,
            "shipping_price": shipping_price,
            "tax_price": total_tax,
            "total_price": total_price,
            "financial_status": financial_status,
            "fulfillment_status": fulfillment_status,
            "shipping_carrier": shipping_carrier,
            "shipment_status": shipment_status,
            "cancelled_at": cancelled_at,
            "created_at": order_time,
            "updated_at": order_time + timedelta(hours=random.randint(0, 48)),
            "last_synced": datetime.utcnow(),
            "source_type": self.source_type,
            "currency": "INR",
            "city": city_data["city"],
            "phone": phone,
            "email": email,
            # Additional fields for ClickHouse
            "state": city_data["state"],
            "zip": zip_code,
            "customer_name": customer_name,
            "sale_initiated_category": sale_category,
            "discount_code": discount_code,
            "product_full_url": None,
            "order_status_url": f"https://clevrrstore.com/orders/{order_id}/status",
        }

        # Generate refund if applicable
        refund_data = None
        refund_lines_data = []

        if financial_status in ["refunded", "partially_refunded"]:
            refund_id = self._next_refund_id()
            refund_date = order_time + timedelta(days=random.randint(3, 14))

            # For partial refund, only refund some items
            refund_items = order_lines_data if financial_status == "refunded" else random.sample(
                order_lines_data,
                max(1, len(order_lines_data) // 2)
            )

            refund_amount = Decimal("0")
            for item in refund_items:
                refund_line_id = self._next_refund_line_id()
                line_refund_amount = item["item_price"] - item["discount_allocated"]

            refund_line = {
                "id": refund_line_id,
                "source_type": self.source_type,
                "quantity": item["quantity"],
                "subtotal_amount": line_refund_amount,
                "total_tax": item["tax_allocated"],
                "order_lines_id": item["order_item_id"],
                "order_id": order_id,
                "refund_id": refund_id,
                "organization_id": self.org_id,
                "created_at": refund_date,
                "product_id": item["product_id"],
                "product_name": item["product_name"],
                "city": city_data["city"],
            }
            refund_lines_data.append(refund_line)
            refund_amount += line_refund_amount

            # Shipping refund (50% chance for full refunds)
            shipping_refund = Decimal("0")
            if financial_status == "refunded" and random.random() > 0.5:
                shipping_refund = shipping_price

            refund_data = {
                "refund_id": refund_id,
                "source_type": self.source_type,
                "order_id": order_id,
                "refund_date": refund_date,
                "refund_amount": refund_amount,
                "shipping_refund": shipping_refund,
                "shipping_refund_tax": Decimal("0"),
                "created_at": refund_date,
                "city": city_data["city"],
                "product_id": refund_items[0]["product_id"] if refund_items else None,
                "product_name": refund_items[0]["product_name"] if refund_items else None,
                "organization_id": self.org_id,
            }

        return {
            "order": order,
            "order_lines": order_lines_data,
            "refund": refund_data,
            "refund_lines": refund_lines_data,
        }

    def get_products(self) -> List[Dict]:
        """Return all generated products."""
        return self.products

    def get_variants(self) -> List[Dict]:
        """Return all generated variants."""
        return self.variants
