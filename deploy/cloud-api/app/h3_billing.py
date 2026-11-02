"""Frozen H3 quotes; one credit equals one yuan. Caller owns the transaction."""
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
from fastapi import HTTPException

def pricing():
    """Read .1's local pricing file for each new quote; no process restart needed."""
    path = Path(os.environ.get('H3_PRICING_FILE', '/opt/cloud-api/h3-pricing.json'))
    try:
        # Defaults live only on the server; an explicitly configured missing file
        # or invalid configuration fails closed rather than silently changing fees.
        if not path.exists() and 'H3_PRICING_FILE' not in os.environ:
            data = {'first_10_seconds_rate': '0.07', 'additional_seconds_rate': '0.09'}
        else:
            data = json.loads(path.read_text(encoding='utf-8'))
        first = Decimal(str(data['first_10_seconds_rate']))
        extra = Decimal(str(data['additional_seconds_rate']))
        if not all(v.is_finite() and 0 < v <= 100 and v == v.quantize(Decimal('.000001')) for v in (first, extra)):
            raise ValueError('Invalid rate')
        rates = {'first_10_seconds_rate': format(first.normalize(), 'f'),
                 'additional_seconds_rate': format(extra.normalize(), 'f'),
                 'threshold_seconds': 10, 'currency': 'CNY', 'duration_basis': 'requested',
                 'rounding': 'ceil_cent', 'credits_per_yuan': '1'}
        version = hashlib.sha256(json.dumps(rates, sort_keys=True).encode()).hexdigest()[:16]
        return {**rates, 'version': 'h3-' + version}
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError):
        raise HTTPException(503, 'H3 定价配置不可用，请稍后重试') from None


def price(seconds, *, rates=None):
    # Only server-persisted operation rates may be supplied by internal callers.
    rates = pricing() if rates is None else rates
    duration = Decimal(str(seconds))
    if not duration.is_finite() or not 0 < duration <= 14:
        raise ValueError('Invalid H3 billing duration')
    amount = (min(duration, Decimal(10)) * Decimal(rates['first_10_seconds_rate'])
              + max(duration - 10, Decimal(0)) * Decimal(rates['additional_seconds_rate']))
    return {**rates, 'credits': str(amount.quantize(Decimal('.01'), rounding=ROUND_CEILING)),
            'duration_seconds': str(duration)}


def frozen(spec):
    # Jobs and quotes created before billing was introduced remain free.
    return spec.get('_billing') or {'credits': '0', 'version': 'legacy-free'}


def reserve(db, job):
    from .services import wallet_for_update, apply_wallet_entry
    amount = Decimal(frozen(job.spec)['credits'])
    if not amount:
        return
    apply_wallet_entry(db, wallet=wallet_for_update(db, job.user_id),
        entry_type='reserve_h3', available_delta=-amount, reserved_delta=amount,
        consumed_delta=0, reference_type='cloud_h3', reference_id=job.id,
        idempotency_key=f'h3:{job.id}:reserve', note='H3 视频预留积分')


def settle(db, job):
    from .services import wallet_for_update, apply_wallet_entry
    amount = Decimal(frozen(job.spec)['credits'])
    if not amount or job.status not in {'succeeded', 'failed', 'cancelled'}:
        return
    success = job.status == 'succeeded'
    apply_wallet_entry(db, wallet=wallet_for_update(db, job.user_id),
        entry_type='consume_h3' if success else 'release_h3',
        available_delta=0 if success else amount, reserved_delta=-amount,
        consumed_delta=amount if success else 0, reference_type='cloud_h3', reference_id=job.id,
        idempotency_key=f'h3:{job.id}:settle', note='H3 视频成功扣费' if success else 'H3 失败或取消释放积分')
