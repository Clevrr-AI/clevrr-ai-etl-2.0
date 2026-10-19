"""
Demo Data Generator Configuration
Contains all seed data and configuration for generating realistic demo data.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Any

# =============================================================================
# DEMO ORGANIZATION CONFIGURATION
# =============================================================================

DEMO_ORG_ID = "getclevrr.com"
DEMO_SOURCE_TYPE = "shopify"
DEMO_META_SOURCE_TYPE = "meta_ads"
DEMO_DELHIVERY_SOURCE_TYPE = "delhivery"

# Connector Type IDs (matching connector_types table)
SHOPIFY_CONNECTOR_TYPE_ID = 1
GOOGLE_ADS_CONNECTOR_TYPE_ID = 2
META_ADS_CONNECTOR_TYPE_ID = 3
DELHIVERY_CONNECTOR_TYPE_ID = 4

# Data Generation Settings
BACKFILL_START_DATE = datetime(2025, 12, 1)
DAILY_ORDER_MIN = 150
DAILY_ORDER_MAX = 300

# =============================================================================
# PRODUCT CATALOG - D2C Fashion Brand "ClevrrStyle"
# =============================================================================

PRODUCT_CATALOG: List[Dict[str, Any]] = [
    # T-Shirts
    {"name": "Classic Cotton Crew Neck T-Shirt", "category": "T-Shirts", "base_price": 799, "cost": 280, "variants": ["S", "M", "L", "XL", "XXL"], "colors": ["Black", "White", "Navy", "Grey"]},
    {"name": "Premium Oversized T-Shirt", "category": "T-Shirts", "base_price": 1199, "cost": 420, "variants": ["S", "M", "L", "XL"], "colors": ["Black", "White", "Olive", "Beige"]},
    {"name": "Graphic Print Street Tee", "category": "T-Shirts", "base_price": 999, "cost": 350, "variants": ["S", "M", "L", "XL"], "colors": ["Black", "White", "Charcoal"]},
    {"name": "V-Neck Essential T-Shirt", "category": "T-Shirts", "base_price": 699, "cost": 245, "variants": ["S", "M", "L", "XL", "XXL"], "colors": ["Black", "White", "Maroon", "Navy"]},
    {"name": "Acid Wash Vintage Tee", "category": "T-Shirts", "base_price": 1299, "cost": 455, "variants": ["S", "M", "L", "XL"], "colors": ["Grey", "Blue", "Pink"]},

    # Shirts
    {"name": "Oxford Button-Down Shirt", "category": "Shirts", "base_price": 1899, "cost": 665, "variants": ["S", "M", "L", "XL", "XXL"], "colors": ["White", "Blue", "Pink", "Grey"]},
    {"name": "Linen Summer Shirt", "category": "Shirts", "base_price": 2199, "cost": 770, "variants": ["S", "M", "L", "XL"], "colors": ["White", "Beige", "Light Blue", "Olive"]},
    {"name": "Flannel Check Shirt", "category": "Shirts", "base_price": 1699, "cost": 595, "variants": ["S", "M", "L", "XL"], "colors": ["Red Check", "Blue Check", "Green Check"]},
    {"name": "Mandarin Collar Kurta Shirt", "category": "Shirts", "base_price": 1499, "cost": 525, "variants": ["S", "M", "L", "XL", "XXL"], "colors": ["White", "Beige", "Sky Blue", "Mint"]},
    {"name": "Denim Casual Shirt", "category": "Shirts", "base_price": 1999, "cost": 700, "variants": ["S", "M", "L", "XL"], "colors": ["Light Wash", "Medium Wash", "Dark Wash"]},

    # Jeans
    {"name": "Slim Fit Stretch Jeans", "category": "Jeans", "base_price": 2499, "cost": 875, "variants": ["28", "30", "32", "34", "36", "38"], "colors": ["Blue", "Black", "Grey"]},
    {"name": "Relaxed Fit Comfort Jeans", "category": "Jeans", "base_price": 2299, "cost": 805, "variants": ["30", "32", "34", "36", "38"], "colors": ["Blue", "Black"]},
    {"name": "Skinny Fit High Rise Jeans", "category": "Jeans", "base_price": 2699, "cost": 945, "variants": ["26", "28", "30", "32", "34"], "colors": ["Blue", "Black", "Grey"]},
    {"name": "Cargo Denim Joggers", "category": "Jeans", "base_price": 2199, "cost": 770, "variants": ["S", "M", "L", "XL"], "colors": ["Khaki", "Black", "Olive"]},
    {"name": "Ripped Distressed Jeans", "category": "Jeans", "base_price": 2899, "cost": 1015, "variants": ["28", "30", "32", "34", "36"], "colors": ["Light Blue", "Medium Blue"]},

    # Trousers & Chinos
    {"name": "Slim Fit Cotton Chinos", "category": "Trousers", "base_price": 1799, "cost": 630, "variants": ["28", "30", "32", "34", "36", "38"], "colors": ["Khaki", "Navy", "Olive", "Grey", "Beige"]},
    {"name": "Formal Wool Blend Trousers", "category": "Trousers", "base_price": 2999, "cost": 1050, "variants": ["30", "32", "34", "36", "38"], "colors": ["Black", "Navy", "Charcoal"]},
    {"name": "Pleated Wide Leg Pants", "category": "Trousers", "base_price": 2499, "cost": 875, "variants": ["S", "M", "L", "XL"], "colors": ["Beige", "Black", "Brown"]},
    {"name": "Ankle Length Cropped Pants", "category": "Trousers", "base_price": 1999, "cost": 700, "variants": ["28", "30", "32", "34", "36"], "colors": ["Black", "Navy", "Grey"]},

    # Hoodies & Sweatshirts
    {"name": "Classic Pullover Hoodie", "category": "Hoodies", "base_price": 1999, "cost": 700, "variants": ["S", "M", "L", "XL", "XXL"], "colors": ["Black", "Grey", "Navy", "Maroon"]},
    {"name": "Zip-Up Tech Fleece Hoodie", "category": "Hoodies", "base_price": 2499, "cost": 875, "variants": ["S", "M", "L", "XL"], "colors": ["Black", "Grey", "Navy"]},
    {"name": "Oversized Drop Shoulder Sweatshirt", "category": "Hoodies", "base_price": 1799, "cost": 630, "variants": ["S", "M", "L", "XL"], "colors": ["Black", "White", "Beige", "Olive"]},
    {"name": "Crewneck Logo Sweatshirt", "category": "Hoodies", "base_price": 1599, "cost": 560, "variants": ["S", "M", "L", "XL", "XXL"], "colors": ["Black", "Grey", "Navy", "White"]},

    # Jackets
    {"name": "Lightweight Bomber Jacket", "category": "Jackets", "base_price": 3499, "cost": 1225, "variants": ["S", "M", "L", "XL"], "colors": ["Black", "Olive", "Navy"]},
    {"name": "Denim Trucker Jacket", "category": "Jackets", "base_price": 2999, "cost": 1050, "variants": ["S", "M", "L", "XL"], "colors": ["Light Wash", "Medium Wash", "Black"]},
    {"name": "Puffer Quilted Jacket", "category": "Jackets", "base_price": 4499, "cost": 1575, "variants": ["S", "M", "L", "XL"], "colors": ["Black", "Navy", "Olive"]},
    {"name": "Windbreaker Sports Jacket", "category": "Jackets", "base_price": 2499, "cost": 875, "variants": ["S", "M", "L", "XL"], "colors": ["Black", "Navy", "Red", "Grey"]},

    # Shorts
    {"name": "Cotton Chino Shorts", "category": "Shorts", "base_price": 1299, "cost": 455, "variants": ["28", "30", "32", "34", "36"], "colors": ["Khaki", "Navy", "Olive", "Grey"]},
    {"name": "Athletic Running Shorts", "category": "Shorts", "base_price": 999, "cost": 350, "variants": ["S", "M", "L", "XL"], "colors": ["Black", "Navy", "Grey"]},
    {"name": "Denim Cut-Off Shorts", "category": "Shorts", "base_price": 1499, "cost": 525, "variants": ["28", "30", "32", "34"], "colors": ["Light Blue", "Medium Blue", "Black"]},
    {"name": "Swim Beach Shorts", "category": "Shorts", "base_price": 1199, "cost": 420, "variants": ["S", "M", "L", "XL"], "colors": ["Navy", "Black", "Tropical Print", "Blue Stripe"]},

    # Accessories
    {"name": "Leather Belt Classic", "category": "Accessories", "base_price": 899, "cost": 315, "variants": ["S", "M", "L"], "colors": ["Black", "Brown", "Tan"]},
    {"name": "Canvas Web Belt", "category": "Accessories", "base_price": 599, "cost": 210, "variants": ["One Size"], "colors": ["Black", "Navy", "Olive", "Khaki"]},
    {"name": "Cotton Baseball Cap", "category": "Accessories", "base_price": 699, "cost": 245, "variants": ["One Size"], "colors": ["Black", "White", "Navy", "Grey", "Beige"]},
    {"name": "Beanie Winter Cap", "category": "Accessories", "base_price": 499, "cost": 175, "variants": ["One Size"], "colors": ["Black", "Grey", "Navy", "Maroon"]},
    {"name": "Crossbody Sling Bag", "category": "Accessories", "base_price": 1499, "cost": 525, "variants": ["One Size"], "colors": ["Black", "Brown", "Tan"]},
    {"name": "Canvas Tote Bag", "category": "Accessories", "base_price": 999, "cost": 350, "variants": ["One Size"], "colors": ["Natural", "Black", "Navy"]},
    {"name": "Wallet Bi-Fold Leather", "category": "Accessories", "base_price": 1299, "cost": 455, "variants": ["One Size"], "colors": ["Black", "Brown", "Tan"]},
    {"name": "Aviator Sunglasses", "category": "Accessories", "base_price": 1999, "cost": 700, "variants": ["One Size"], "colors": ["Gold/Green", "Silver/Blue", "Black/Grey"]},

    # Ethnic Wear
    {"name": "Printed Nehru Jacket", "category": "Ethnic", "base_price": 2999, "cost": 1050, "variants": ["S", "M", "L", "XL"], "colors": ["Maroon", "Navy", "Black", "Beige"]},
    {"name": "Cotton Kurta Set", "category": "Ethnic", "base_price": 2499, "cost": 875, "variants": ["S", "M", "L", "XL", "XXL"], "colors": ["White", "Blue", "Green", "Maroon"]},
    {"name": "Embroidered Festive Kurta", "category": "Ethnic", "base_price": 3499, "cost": 1225, "variants": ["S", "M", "L", "XL"], "colors": ["Cream", "Maroon", "Navy"]},

    # Women's Wear (for broader catalog)
    {"name": "Floral Print Maxi Dress", "category": "Dresses", "base_price": 2499, "cost": 875, "variants": ["XS", "S", "M", "L", "XL"], "colors": ["Blue Floral", "Pink Floral", "Black Floral"]},
    {"name": "Bodycon Mini Dress", "category": "Dresses", "base_price": 1999, "cost": 700, "variants": ["XS", "S", "M", "L"], "colors": ["Black", "Red", "Navy"]},
    {"name": "Cotton A-Line Kurti", "category": "Kurtis", "base_price": 1299, "cost": 455, "variants": ["S", "M", "L", "XL", "XXL"], "colors": ["Blue", "Green", "Yellow", "Pink", "White"]},
    {"name": "Printed Palazzo Pants", "category": "Bottoms", "base_price": 1199, "cost": 420, "variants": ["S", "M", "L", "XL"], "colors": ["Black", "Blue Print", "Maroon Print"]},
    {"name": "High Waist Leggings", "category": "Bottoms", "base_price": 899, "cost": 315, "variants": ["S", "M", "L", "XL"], "colors": ["Black", "Navy", "Grey"]},
]

# =============================================================================
# INDIAN CITIES DISTRIBUTION (Weighted by D2C order volume)
# =============================================================================

INDIAN_CITIES: List[Dict[str, Any]] = [
    # Metro Cities (60% of orders)
    {"city": "Mumbai", "state": "Maharashtra", "weight": 15, "tier": 1, "pin_prefix": "400"},
    {"city": "Delhi", "state": "Delhi", "weight": 14, "tier": 1, "pin_prefix": "110"},
    {"city": "Bangalore", "state": "Karnataka", "weight": 12, "tier": 1, "pin_prefix": "560"},
    {"city": "Hyderabad", "state": "Telangana", "weight": 8, "tier": 1, "pin_prefix": "500"},
    {"city": "Chennai", "state": "Tamil Nadu", "weight": 6, "tier": 1, "pin_prefix": "600"},
    {"city": "Kolkata", "state": "West Bengal", "weight": 5, "tier": 1, "pin_prefix": "700"},

    # Tier 2 Cities (25% of orders)
    {"city": "Pune", "state": "Maharashtra", "weight": 4, "tier": 2, "pin_prefix": "411"},
    {"city": "Ahmedabad", "state": "Gujarat", "weight": 3, "tier": 2, "pin_prefix": "380"},
    {"city": "Jaipur", "state": "Rajasthan", "weight": 2.5, "tier": 2, "pin_prefix": "302"},
    {"city": "Lucknow", "state": "Uttar Pradesh", "weight": 2, "tier": 2, "pin_prefix": "226"},
    {"city": "Chandigarh", "state": "Chandigarh", "weight": 2, "tier": 2, "pin_prefix": "160"},
    {"city": "Kochi", "state": "Kerala", "weight": 1.5, "tier": 2, "pin_prefix": "682"},
    {"city": "Indore", "state": "Madhya Pradesh", "weight": 1.5, "tier": 2, "pin_prefix": "452"},
    {"city": "Nagpur", "state": "Maharashtra", "weight": 1.5, "tier": 2, "pin_prefix": "440"},
    {"city": "Surat", "state": "Gujarat", "weight": 1.5, "tier": 2, "pin_prefix": "395"},
    {"city": "Coimbatore", "state": "Tamil Nadu", "weight": 1.5, "tier": 2, "pin_prefix": "641"},
    {"city": "Bhopal", "state": "Madhya Pradesh", "weight": 1, "tier": 2, "pin_prefix": "462"},
    {"city": "Visakhapatnam", "state": "Andhra Pradesh", "weight": 1, "tier": 2, "pin_prefix": "530"},
    {"city": "Patna", "state": "Bihar", "weight": 1, "tier": 2, "pin_prefix": "800"},
    {"city": "Vadodara", "state": "Gujarat", "weight": 1, "tier": 2, "pin_prefix": "390"},

    # Tier 3 Cities (15% of orders)
    {"city": "Guwahati", "state": "Assam", "weight": 0.8, "tier": 3, "pin_prefix": "781"},
    {"city": "Ranchi", "state": "Jharkhand", "weight": 0.7, "tier": 3, "pin_prefix": "834"},
    {"city": "Bhubaneswar", "state": "Odisha", "weight": 0.7, "tier": 3, "pin_prefix": "751"},
    {"city": "Dehradun", "state": "Uttarakhand", "weight": 0.6, "tier": 3, "pin_prefix": "248"},
    {"city": "Raipur", "state": "Chhattisgarh", "weight": 0.5, "tier": 3, "pin_prefix": "492"},
    {"city": "Mysore", "state": "Karnataka", "weight": 0.5, "tier": 3, "pin_prefix": "570"},
    {"city": "Mangalore", "state": "Karnataka", "weight": 0.4, "tier": 3, "pin_prefix": "575"},
    {"city": "Thiruvananthapuram", "state": "Kerala", "weight": 0.4, "tier": 3, "pin_prefix": "695"},
    {"city": "Goa", "state": "Goa", "weight": 0.4, "tier": 3, "pin_prefix": "403"},
    {"city": "Shimla", "state": "Himachal Pradesh", "weight": 0.3, "tier": 3, "pin_prefix": "171"},
]

# =============================================================================
# META ADS CAMPAIGN CONFIGURATION
# =============================================================================

META_AD_CAMPAIGNS: List[Dict[str, Any]] = [
    {
        "campaign_name": "TOF - Broad Prospecting",
        "campaign_id": "120001",
        "objective": "CONVERSIONS",
        "status": "ACTIVE",
        "daily_budget_range": (8000, 15000),
        "ad_sets": [
            {"name": "Lookalike 1% - Purchase", "id": "230001", "targeting": "lookalike"},
            {"name": "Interest - Fashion Enthusiasts", "id": "230002", "targeting": "interest"},
            {"name": "Interest - Online Shoppers", "id": "230003", "targeting": "interest"},
            {"name": "Broad - 18-45 Metro", "id": "230004", "targeting": "broad"},
        ]
    },
    {
        "campaign_name": "MOF - Website Visitors",
        "campaign_id": "120002",
        "objective": "CONVERSIONS",
        "status": "ACTIVE",
        "daily_budget_range": (5000, 10000),
        "ad_sets": [
            {"name": "Retarget - View Content 7D", "id": "230005", "targeting": "retargeting"},
            {"name": "Retarget - Add to Cart 14D", "id": "230006", "targeting": "retargeting"},
            {"name": "Retarget - Page Visitors 30D", "id": "230007", "targeting": "retargeting"},
        ]
    },
    {
        "campaign_name": "BOF - Cart Abandoners",
        "campaign_id": "120003",
        "objective": "CONVERSIONS",
        "status": "ACTIVE",
        "daily_budget_range": (3000, 6000),
        "ad_sets": [
            {"name": "DPA - Cart Abandoners 7D", "id": "230008", "targeting": "dpa"},
            {"name": "DPA - Checkout Abandoners 14D", "id": "230009", "targeting": "dpa"},
        ]
    },
    {
        "campaign_name": "Brand Awareness - Video",
        "campaign_id": "120004",
        "objective": "BRAND_AWARENESS",
        "status": "ACTIVE",
        "daily_budget_range": (2000, 5000),
        "ad_sets": [
            {"name": "Video Views - 18-35 Interest", "id": "230010", "targeting": "interest"},
            {"name": "Video Views - Broad Reach", "id": "230011", "targeting": "broad"},
        ]
    },
    {
        "campaign_name": "Sale - Seasonal Promo",
        "campaign_id": "120005",
        "objective": "CONVERSIONS",
        "status": "ACTIVE",
        "daily_budget_range": (4000, 12000),
        "ad_sets": [
            {"name": "Sale - All Audiences", "id": "230012", "targeting": "broad"},
            {"name": "Sale - Past Purchasers", "id": "230013", "targeting": "retargeting"},
            {"name": "Sale - High Intent Lookalike", "id": "230014", "targeting": "lookalike"},
        ]
    },
]

META_AD_CREATIVES: List[Dict[str, Any]] = [
    {"name": "Carousel - New Arrivals", "type": "carousel", "ctr_modifier": 1.1},
    {"name": "Single Image - Hero Product", "type": "single_image", "ctr_modifier": 1.0},
    {"name": "Video - Brand Story 15s", "type": "video", "ctr_modifier": 1.2},
    {"name": "Collection - Summer Edit", "type": "collection", "ctr_modifier": 1.15},
    {"name": "DPA - Dynamic Product", "type": "dpa", "ctr_modifier": 1.3},
    {"name": "Single Image - Sale Banner", "type": "single_image", "ctr_modifier": 0.95},
    {"name": "Video - Product Demo 30s", "type": "video", "ctr_modifier": 1.1},
    {"name": "Carousel - Best Sellers", "type": "carousel", "ctr_modifier": 1.05},
]

# Meta Ads Performance Benchmarks (Indian D2C - Mediocre Brand)
META_ADS_BENCHMARKS = {
    "cpm_range": (120, 280),  # INR
    "ctr_range": (0.8, 2.5),  # %
    "cpc_range": (8, 35),  # INR
    "conversion_rate_range": (1.5, 4.5),  # %
    "roas_range": (2.2, 4.5),
    "landing_page_view_rate": (0.6, 0.85),  # of clicks
    "add_to_cart_rate": (0.08, 0.18),  # of landing page views
    "checkout_initiated_rate": (0.35, 0.55),  # of add to cart
    "purchase_rate": (0.45, 0.70),  # of checkout initiated
}

# =============================================================================
# DELHIVERY CONFIGURATION
# =============================================================================

DELHIVERY_STATUS_FLOW = [
    {"status": "Manifested", "code": "UD", "description": "Shipment manifested", "day_offset": 0},
    {"status": "Picked Up", "code": "PU", "description": "Shipment picked up from seller", "day_offset": 1},
    {"status": "In Transit", "code": "IT", "description": "In transit to destination hub", "day_offset": 2},
    {"status": "Reached Destination Hub", "code": "RDH", "description": "Reached destination city hub", "day_offset": 3},
    {"status": "Out for Delivery", "code": "OFD", "description": "Out for delivery", "day_offset": 4},
    {"status": "Delivered", "code": "DL", "description": "Delivered to customer", "day_offset": 4},
]

DELHIVERY_RTO_FLOW = [
    {"status": "Manifested", "code": "UD", "description": "Shipment manifested", "day_offset": 0},
    {"status": "Picked Up", "code": "PU", "description": "Shipment picked up from seller", "day_offset": 1},
    {"status": "In Transit", "code": "IT", "description": "In transit to destination hub", "day_offset": 2},
    {"status": "Reached Destination Hub", "code": "RDH", "description": "Reached destination city hub", "day_offset": 3},
    {"status": "Out for Delivery", "code": "OFD", "description": "Out for delivery", "day_offset": 4},
    {"status": "Delivery Attempted", "code": "DA", "description": "Delivery attempted - customer unavailable", "day_offset": 4},
    {"status": "RTO Initiated", "code": "RT", "description": "Return to origin initiated", "day_offset": 5},
    {"status": "RTO In Transit", "code": "RIT", "description": "RTO shipment in transit", "day_offset": 6},
    {"status": "RTO Delivered", "code": "RD", "description": "Returned to seller", "day_offset": 8},
]

DELHIVERY_HUBS = [
    {"city": "Mumbai", "hub": "BOM/MH/MUM"},
    {"city": "Delhi", "hub": "DEL/DL/DEL"},
    {"city": "Bangalore", "hub": "BLR/KA/BLR"},
    {"city": "Hyderabad", "hub": "HYD/TS/HYD"},
    {"city": "Chennai", "hub": "MAA/TN/CHE"},
    {"city": "Kolkata", "hub": "CCU/WB/KOL"},
    {"city": "Pune", "hub": "PNQ/MH/PUN"},
    {"city": "Ahmedabad", "hub": "AMD/GJ/AHM"},
]

# Delivery success rates
DELIVERY_SUCCESS_RATE = 0.85  # 85% delivered
RTO_RATE = 0.08  # 8% RTO
IN_TRANSIT_RATE = 0.07  # 7% still in transit

# =============================================================================
# ORDER GENERATION CONFIGURATION
# =============================================================================

# Hour weights for order distribution (IST)
ORDER_HOUR_WEIGHTS = {
    0: 0.5, 1: 0.3, 2: 0.2, 3: 0.1, 4: 0.1, 5: 0.2,
    6: 0.4, 7: 0.6, 8: 0.8, 9: 1.2, 10: 1.8, 11: 2.0,
    12: 1.8, 13: 1.5, 14: 1.3, 15: 1.2, 16: 1.3, 17: 1.5,
    18: 1.8, 19: 2.2, 20: 2.5, 21: 2.8, 22: 2.2, 23: 1.2,
}

# Day of week multipliers (0=Monday, 6=Sunday)
DAY_OF_WEEK_MULTIPLIERS = {
    0: 0.9,   # Monday
    1: 0.95,  # Tuesday
    2: 1.0,   # Wednesday
    3: 1.0,   # Thursday
    4: 1.1,   # Friday
    5: 1.25,  # Saturday
    6: 1.2,   # Sunday
}

# Financial status distribution
FINANCIAL_STATUS_DISTRIBUTION = {
    "paid": 0.72,
    "pending": 0.15,
    "refunded": 0.08,
    "voided": 0.03,
    "partially_refunded": 0.02,
}

# Fulfillment status distribution (for paid orders)
FULFILLMENT_STATUS_DISTRIBUTION = {
    "fulfilled": 0.75,
    "partial": 0.10,
    "unfulfilled": 0.10,
    "restocked": 0.05,
}

# Sale initiated category distribution
SALE_CATEGORY_DISTRIBUTION = {
    "PAID-META": 0.35,
    "PAID-GOOGLE": 0.20,
    "ORGANIC": 0.18,
    "DIRECT": 0.12,
    "SOCIAL": 0.10,
    "OTHERS": 0.05,
}

# Discount code distribution
DISCOUNT_CODES = [
    {"code": "WELCOME10", "discount_percent": 10, "usage_weight": 0.25},
    {"code": "SAVE15", "discount_percent": 15, "usage_weight": 0.15},
    {"code": "FIRST20", "discount_percent": 20, "usage_weight": 0.10},
    {"code": "FLASH25", "discount_percent": 25, "usage_weight": 0.05},
    {"code": "FESTIVE30", "discount_percent": 30, "usage_weight": 0.03},
    {"code": None, "discount_percent": 0, "usage_weight": 0.42},  # No discount
]

# Average items per order distribution
ITEMS_PER_ORDER_DISTRIBUTION = {
    1: 0.45,
    2: 0.30,
    3: 0.15,
    4: 0.07,
    5: 0.03,
}

# =============================================================================
# CUSTOMER NAME GENERATION
# =============================================================================

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Reyansh", "Ayaan", "Atharv",
    "Ananya", "Diya", "Aditi", "Kiara", "Myra", "Sara", "Aanya", "Ishita",
    "Rohan", "Karthik", "Rahul", "Amit", "Priya", "Sneha", "Neha", "Pooja",
    "Raj", "Vikram", "Sanjay", "Deepak", "Ankita", "Shruti", "Kavya", "Riya",
    "Mohammed", "Ali", "Fatima", "Zara", "Imran", "Ayesha", "Hassan", "Mariam",
    "Siddharth", "Arnav", "Dhruv", "Ishaan", "Tanvi", "Nisha", "Meera", "Lakshmi",
]

LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Singh", "Kumar", "Patel", "Shah", "Mehta",
    "Joshi", "Agarwal", "Mishra", "Pandey", "Reddy", "Rao", "Nair", "Menon",
    "Iyer", "Krishnan", "Pillai", "Mukherjee", "Banerjee", "Chatterjee", "Das",
    "Kapoor", "Malhotra", "Chopra", "Khanna", "Bhatia", "Sinha", "Saxena",
    "Khan", "Ahmed", "Hussain", "Sheikh", "Qureshi", "Ansari", "Siddiqui",
    "Desai", "Patil", "Kulkarni", "Jain", "Agrawal", "Rastogi", "Mahajan",
]

# =============================================================================
# UTILITY FUNCTIONS FOR CONFIG
# =============================================================================

def get_total_city_weight() -> float:
    """Returns total weight for city distribution normalization."""
    return sum(city["weight"] for city in INDIAN_CITIES)

def get_product_count() -> int:
    """Returns total number of products in catalog."""
    return len(PRODUCT_CATALOG)

def get_variant_count() -> int:
    """Returns total number of variants across all products."""
    total = 0
    for product in PRODUCT_CATALOG:
        total += len(product["variants"]) * len(product["colors"])
    return total
