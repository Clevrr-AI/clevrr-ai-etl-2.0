"""Convert Python values to what a ClickHouse column takes, the way legacy's load did.

Legacy wrote pandas frames as Parquet. ClickHouse's Parquet input turned a NULL into the column
default for a non-Nullable column (`input_format_null_as_default`): '' for a String, 0 for a
number, 1970-01-01 for a DateTime. It also wrote an integer into a String column as its digits.
clickhouse-connect does none of this: it fails on None for a non-Nullable column. `coercer`
returns one column type's conversion, so every pipeline writes the values legacy wrote.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

Coerce = Callable[[Any], Any]
_WRAPPER = re.compile(r"^(LowCardinality|Nullable)\((.*)\)$")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def parse(ch_type: str) -> tuple[str, bool]:
    """The base type without LowCardinality/Nullable, and whether NULL is allowed."""
    nullable = False
    while match := _WRAPPER.match(ch_type):
        nullable = nullable or match.group(1) == "Nullable"
        ch_type = match.group(2)
    return ch_type, nullable


def _string(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value)
    return str(value)


def _integer(value: Any) -> int:
    if isinstance(value, Decimal | float) and value != int(value):
        raise ValueError(f"{value!r} is not a whole number")
    return int(value)


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _datetime(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    raise ValueError(f"{value!r} is not a date-time")


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _kind(base: str) -> tuple[Coerce | None, Any]:
    """(conversion, default) for a base type; (None, None) passes values through."""
    if base == "String" or base.startswith("FixedString"):
        return _string, ""
    if re.match(r"^U?Int\d+$", base):
        return _integer, 0
    if base.startswith("Float"):
        return float, 0.0
    if base.startswith("Decimal"):
        return _decimal, Decimal(0)
    if base.startswith("DateTime"):
        return _datetime, EPOCH
    if base.startswith("Date"):
        return _date, date(1970, 1, 1)
    if base == "Bool":
        return bool, False
    return None, None


def coercer(ch_type: str) -> Coerce:
    base, nullable = parse(ch_type)
    convert, default = _kind(base)

    def coerce(value: Any) -> Any:
        if value is None:
            return None if nullable else default
        return value if convert is None else convert(value)

    return coerce
