"""Canonical Indian state/UT names (copied unchanged from legacy utils/india_states.py)."""

# Canonical Indian state/UT names.
#
# Three independent sources of state names meet on the Sales dashboard's
# "Top Performing States and Cities" table and have to agree:
#   1. Meta Insights `breakdowns=region`  -> ad spend      (this module normalizes it)
#   2. Shopify shipping_address.province  -> orders/revenue
#   3. postalcodes-india (browser)        -> the row labels the table renders
#
# They disagree on renamed states (Orissa/Odisha, Pondicherry/Puducherry),
# on "&" vs "and", and on whether Delhi carries the "NCT of" prefix. Spend
# normalized here is matched against the table's labels by
# src/utils/indiaStateNormalizer.ts on the frontend, which applies the same
# key rule — keep the two alias tables in sync.

import re

# Canonical display names: 28 states + 8 union territories, current names.
_CANONICAL = [
    "Andaman and Nicobar Islands",
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chandigarh",
    "Chhattisgarh",
    "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jammu and Kashmir",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Ladakh",
    "Lakshadweep",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Puducherry",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
]


def state_key(name: object) -> str:
    """Collapse a state name to a comparison key.

    Uppercases, expands '&' to 'and', then strips every non-letter AND the word
    'and' itself, so "Andaman & Nicobar Islands", "Andaman and Nicobar Islands"
    and "ANDAMAN-AND-NICOBAR-ISLANDS" all key alike.
    """
    s = str(name or "").strip().upper().replace("&", " AND ")
    s = re.sub(r"\bAND\b", " ", s)
    # Meta suffixes "region" onto names that straddle a border, so India's Punjab
    # comes back as "Punjab region" (vs Pakistan's). Confirmed live 2026-09-10:
    # ~8.7L of shapercult.com's spend arrived under that spelling and matched no
    # state. No Indian state or UT contains "region", and a foreign name that does
    # still fails to match one, so stripping it is safe.
    s = re.sub(r"\bREGION\b", " ", s)
    return re.sub(r"[^A-Z]", "", s)


# Historic names, common misspellings and prefixed forms seen in Meta region
# strings and Shopify province values, keyed by state_key().
_ALIASES = {
    # renamed states
    "ORISSA": "Odisha",
    "PONDICHERRY": "Puducherry",
    "PONDICHERY": "Puducherry",
    "UTTARANCHAL": "Uttarakhand",
    # Delhi, with and without the administrative prefix
    "NCTOFDELHI": "Delhi",
    "NATIONALCAPITALTERRITORYOFDELHI": "Delhi",
    "DELHINCT": "Delhi",
    "NEWDELHI": "Delhi",
    # merged / partially-named union territories
    "DADRANAGARHAVELI": "Dadra and Nagar Haveli and Daman and Diu",
    "DAMANDIU": "Dadra and Nagar Haveli and Daman and Diu",
    "ANDAMANNICOBAR": "Andaman and Nicobar Islands",
    "ANDAMANNICOBARISLAND": "Andaman and Nicobar Islands",
    # spacing / spelling variants
    "TAMILNADU": "Tamil Nadu",
    "CHATTISGARH": "Chhattisgarh",
    "UTTARPRADESHUP": "Uttar Pradesh",
    "JAMMUKASHMIRSTATE": "Jammu and Kashmir",
}

_BY_KEY = {state_key(name): name for name in _CANONICAL}
_BY_KEY.update({key: _BY_KEY[state_key(target)] for key, target in _ALIASES.items()})

# Meta labels rows it could not geolocate "Unknown"; keep that as its own bucket
# rather than dropping it, so state spend can be reconciled against account spend.
UNKNOWN_STATE = "Unknown"


def canonical_state(name: object) -> str:
    """Map any spelling of an Indian state/UT to its canonical display name.

    Unrecognized non-empty values are returned title-cased and untouched — they
    are usually non-India regions from an account running international ads, and
    silently folding them into 'Unknown' would hide real spend.
    """
    raw = str(name or "").strip()
    if not raw:
        return UNKNOWN_STATE
    if raw.lower() == "unknown":
        return UNKNOWN_STATE
    return _BY_KEY.get(state_key(raw), raw)
