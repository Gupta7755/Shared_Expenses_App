"""
currency.py — Multi-currency conversion engine for Splitly.

Rates are stored in DB (ExchangeRate model) and admin-updatable.
Falls back to hardcoded defaults if DB record is missing.
"""
from decimal import Decimal, ROUND_HALF_UP

# Hardcoded fallback rates (1 unit → INR)
DEFAULT_RATES = {
    'INR': Decimal('1.000000'),
    'USD': Decimal('84.000000'),
    'EUR': Decimal('91.000000'),
    'GBP': Decimal('107.000000'),
    'AED': Decimal('22.870000'),
}

CURRENCY_SYMBOLS = {
    'INR': '₹',
    'USD': '$',
    'EUR': '€',
    'GBP': '£',
    'AED': 'د.إ',
}

SUPPORTED_CURRENCIES = list(DEFAULT_RATES.keys())


def get_rate(currency: str) -> Decimal:
    """
    Returns the exchange rate for 1 unit of `currency` in INR.
    First checks DB, falls back to hardcoded defaults.
    """
    currency = currency.upper()
    try:
        from splitly.models import Currency
        obj = Currency.objects.get(currency=currency)
        return obj.rate_to_inr
    except Exception:
        return DEFAULT_RATES.get(currency, Decimal('1.0'))


def convert_to_inr(amount, currency: str) -> Decimal:
    """Converts `amount` in `currency` to INR."""
    amount = Decimal(str(amount))
    rate = get_rate(currency)
    return (amount * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def convert_from_inr(amount_inr, target_currency: str) -> Decimal:
    """Converts `amount_inr` (INR) to `target_currency`."""
    amount_inr = Decimal(str(amount_inr))
    rate = get_rate(target_currency)
    if rate == 0:
        return Decimal('0.00')
    return (amount_inr / rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def convert_between(amount, from_currency: str, to_currency: str) -> Decimal:
    """Converts directly between two non-INR currencies via INR as pivot."""
    inr_value = convert_to_inr(amount, from_currency)
    return convert_from_inr(inr_value, to_currency)


def format_currency(amount, currency: str) -> str:
    """Returns a human-readable string like '₹1,200.00' or '$84.00'."""
    symbol = CURRENCY_SYMBOLS.get(currency.upper(), currency)
    try:
        formatted = f"{float(amount):,.2f}"
    except (ValueError, TypeError):
        formatted = "0.00"
    return f"{symbol}{formatted}"


def get_all_rates() -> dict:
    """Returns a dict of all current rates {currency: rate_to_inr}."""
    rates = dict(DEFAULT_RATES)
    try:
        from splitly.models import Currency
        for obj in Currency.objects.all():
            rates[obj.currency] = obj.rate_to_inr
    except Exception:
        pass
    return rates


def seed_default_rates(user=None):
    """
    Seeds the ExchangeRate table with default values.
    Called from migrations or admin setup. Skips existing entries.
    """
    from splitly.models import Currency
    for currency, rate in DEFAULT_RATES.items():
        Currency.objects.get_or_create(
            currency=currency,
            defaults={'rate_to_inr': rate, 'updated_by': user}
        )
