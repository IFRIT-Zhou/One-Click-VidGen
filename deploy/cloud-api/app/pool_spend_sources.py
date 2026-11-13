"""Only provider-reported actual USD costs; never retail prices or balances."""
from decimal import Decimal, InvalidOperation

import httpx


class SpendSourceError(RuntimeError):
    pass


def amount_usd(value):
    if value is None or isinstance(value, (bool, float)):
        raise SpendSourceError("cost_missing_or_inexact")
    try:
        amount = Decimal(value)
    except (ValueError, TypeError, InvalidOperation):
        raise SpendSourceError("cost_invalid") from None
    if not amount.is_finite() or amount < 0:
        raise SpendSourceError("cost_invalid")
    return amount


def request_json(method, url, key, **kwargs):
    import json
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False) as client:
            response = client.request(method, url, headers={"Authorization": "Bearer " + key}, **kwargs)
        if response.status_code != 200:
            raise SpendSourceError("cost_http_error")
        data = json.loads(response.content, parse_float=Decimal)
    except (httpx.HTTPError, ValueError):
        raise SpendSourceError("cost_query_failed") from None
    if not isinstance(data, dict):
        raise SpendSourceError("cost_response_invalid")
    return data


def ican_total_usd(key):
    data = request_json("GET", "https://ican-gpt.com/v1/usage", key)
    if data.get("isValid") is not True or data.get("unit") != "USD":
        raise SpendSourceError("ican_cost_currency_or_key_invalid")
    usage = data.get("usage")
    total = usage.get("total") if isinstance(usage, dict) else None
    if not isinstance(total, dict):
        raise SpendSourceError("ican_cost_unavailable")
    # actual_cost is the customer's charge. account_cost is provider internal cost.
    return amount_usd(total.get("actual_cost"))
