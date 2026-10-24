from collections import Counter

from aeonlib.skynet import InstrumentPool, InstrumentPools
from django.conf import settings


def allowed_telescope_prefix() -> str:
    """FACILITIES["SKYNET"]["allowed_telescope_prefix"]; empty allows every telescope"""
    facility = getattr(settings, "FACILITIES", {}).get("SKYNET", {})
    return facility.get("allowed_telescope_prefix") or ""


def visible_pools(pools: InstrumentPools) -> list[InstrumentPool]:
    """The pools whose telescopes all start with the allowed prefix"""
    prefix = allowed_telescope_prefix()
    return [
        pool
        for pool in pools.pools
        if pool.instrument_ids
        and all(t.slug.startswith(prefix) for t in pools.pool_telescopes(pool))
    ]


def pool_labels(pools: InstrumentPools, shown: list[InstrumentPool]) -> dict[int, str]:
    """
    Choice labels by grant id: the account name, with the grant type added when
    the user can spend several grants on one account
    """
    per_account = Counter(pool.account_id for pool in shown)
    labels = {}
    for pool in shown:
        account = pools.account(pool)
        label = account.name or account.slug or f"Account {account.id}"
        if per_account[pool.account_id] > 1:
            label = f"{label} ({pool.grant_type})"
        labels[pool.grant_id] = label
    return labels
