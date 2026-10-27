"""
Which of a user's time accounts the TOM offers.

An observation belongs to the owner of the account that funds it, and so do
its data: Skynet's :publish takes the account owner's id. So the TOM offers
the accounts the user owns, and organizations' accounts the user holds a grant
on, each labelled with its owner; never another user's personal account,
which would make the request someone else's. Which telescopes an account
reaches is Skynet's business, through its grants.
"""

from collections import Counter

from aeonlib.skynet import Entity, InstrumentPool, InstrumentPools

ORGANIZATION = "organization"


def visible_pools(pools: InstrumentPools, user_id: int) -> list[InstrumentPool]:
    """
    The pools on accounts the Skynet user user_id owns, or an organization
    owns; each with at least one instrument
    """
    return [
        pool
        for pool in pools.pools
        if pool.instrument_ids and _offered(pools.owner(pool), user_id)
    ]


def _offered(owner: Entity | None, user_id: int) -> bool:
    if owner is None:
        return False
    return owner.id == user_id or owner.entity_type == ORGANIZATION


def owner_label(owner: Entity | None, user_id: int | None = None) -> str:
    """Who an account's observations belong to, for a TOM user"""
    if owner is None:
        return "unknown owner"
    if owner.id == user_id:
        return "yours"
    return owner.name or owner.slug


def pool_labels(
    pools: InstrumentPools, shown: list[InstrumentPool], user_id: int
) -> dict[int, str]:
    """
    Choice labels by grant id: the account name and its owner, "yours" or the
    organization's name; with the grant type added when the user can spend
    several grants on one account
    """
    per_account = Counter(pool.account_id for pool in shown)
    labels = {}
    for pool in shown:
        account = pools.account(pool)
        name = account.name or account.slug or f"Account {account.id}"
        owner = pools.owner(pool)
        owned_by = owner_label(owner, user_id)
        if owner is not None and owner.id != user_id and owned_by == name:
            # An organization's account named after it
            owned_by = ORGANIZATION
        notes = [owned_by]
        if per_account[pool.account_id] > 1:
            notes.append(pool.grant_type)
        labels[pool.grant_id] = f"{name} ({', '.join(notes)})"
    return labels
