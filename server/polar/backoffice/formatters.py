from datetime import datetime as dt
from decimal import Decimal

from polar.kit.currency import format_currency


def datetime(value: dt) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def currency(
    value: int | Decimal | float, currency: str, *, decimal_quantization: bool = True
) -> str:
    return format_currency(value, currency, decimal_quantization=decimal_quantization)


def file_size(size_bytes: int) -> str:
    """Format file size in bytes to human-readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
