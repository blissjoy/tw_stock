"""Shared Taiwanese stock-trading commission and tax estimates."""

TWSE_MAX_COMMISSION_RATE = 0.001425
BROKER_COMMISSION_DISCOUNT = 0.3
MIN_COMMISSION_FEE = 1
TWSE_TRANSACTION_TAX_RATE = 0.003


def estimate_commission_fee(notional: float) -> int:
    """Estimate one-side commission, rounded to whole currency units."""
    raw_fee = notional * TWSE_MAX_COMMISSION_RATE * BROKER_COMMISSION_DISCOUNT
    return max(round(raw_fee), MIN_COMMISSION_FEE)


def estimate_transaction_tax(notional: float) -> int:
    """Estimate seller transaction tax, rounded to whole currency units."""
    return round(notional * TWSE_TRANSACTION_TAX_RATE)
