"""Read upstream wallet balances; never confuse token quotas or RH coins with cash.

RunningHub: official account information endpoint (api-425761030).
ICAN: /v1/usage used by the official KeysView and KeyUsageView clients.
Currency conversion and account/key grouping belong to the monitor configuration.
"""
from decimal import Decimal, InvalidOperation

import httpx


class BalanceQueryError(RuntimeError):
    """Safe machine-readable failure; no upstream body, URL parameters or keys."""


def _wallet(amount, currency):
    if amount is None or isinstance(amount, bool):
        raise BalanceQueryError("wallet_balance_unavailable")
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        raise BalanceQueryError("wallet_balance_invalid") from None
    if not value.is_finite():
        raise BalanceQueryError("wallet_balance_invalid")
    if not isinstance(currency, str) or currency.upper() not in {"USD", "CNY"}:
        raise BalanceQueryError("wallet_currency_unknown")
    return value, currency.upper()


def _query(method, url, key, **kwargs):
    if not isinstance(key, str) or not key.strip():
        raise BalanceQueryError("credential_missing")
    try:
        response = httpx.request(method, url, headers={"Authorization": "Bearer " + key},
                                 timeout=httpx.Timeout(20, connect=8), follow_redirects=False, **kwargs)
        if response.status_code != 200:
            raise BalanceQueryError("balance_http_error")
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        raise BalanceQueryError("balance_request_failed") from None
    if not isinstance(payload, dict):
        raise BalanceQueryError("balance_response_invalid")
    return payload


def query_runninghub(key):
    payload = _query("POST", "https://www.runninghub.ai/uc/openapi/accountStatus", key,
                     json={"apikey": key})
    if payload.get("code") != 0:
        if payload.get("code") == 806:
            raise BalanceQueryError("runninghub_account_not_found")
        raise BalanceQueryError("runninghub_query_rejected")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise BalanceQueryError("balance_response_invalid")
    # SHARED accounts can return coins but null wallet balance/currency.
    # Never assume coins or remainPoint are USD.
    return _wallet(data.get("remainMoney"), data.get("currency"))


def query_ican(key):
    payload = _query("GET", "https://ican-gpt.com/v1/usage", key)
    if payload.get("isValid") is False:
        raise BalanceQueryError("ican_key_invalid")
    # remaining/quota.remaining can be token limits, not the user's wallet.
    return _wallet(payload.get("balance"), payload.get("unit"))
