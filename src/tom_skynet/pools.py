"""
Which of a user's time accounts the TOM offers.

An observation belongs to the owner of the account that funds it, and so do
its data: Skynet's :publish takes the account owner's uid. So the TOM offers
the accounts the user owns, and organizations' accounts the user holds a grant
on, each labelled with its owner; never another user's personal account,
which would make the request someone else's. Which telescopes an account
reaches is Skynet's business, through its grants.

Everything here is keyed by uid, as Skynet addresses it: the grant the form
submits, the account it is on and the entity that owns it.
"""

from collections import Counter

from aeonlib.skynet import Entity, InstrumentPool, InstrumentPools

ORGANIZATION = "organization"


def visible_pools(pools: InstrumentPools, user_uid: str) -> list[InstrumentPool]:
    """
    The pools on accounts the Skynet user user_uid owns, or an organization
    owns; each with at least one instrument
    """
    return [
        pool
        for pool in pools.pools
        if pool.instrument_uids and _offered(pools.owner(pool), user_uid)
    ]


def _offered(owner: Entity | None, user_uid: str) -> bool:
    if owner is None:
        return False
    return owner.uid == user_uid or owner.entity_type == ORGANIZATION


def owner_label(owner: Entity | None, user_uid: str | None = None) -> str:
    """Who an account's observations belong to, for a TOM user"""
    if owner is None:
        return "unknown owner"
    if owner.uid == user_uid:
        return "yours"
    return owner.name or owner.slug


def pool_labels(
    pools: InstrumentPools, shown: list[InstrumentPool], user_uid: str
) -> dict[str, str]:
    """
    Choice labels by grant uid: the account name and its owner, "yours" or the
    organization's name; with the grant type added when the user can spend
    several grants on one account
    """
    per_account = Counter(pool.account_uid for pool in shown)
    labels = {}
    for pool in shown:
        account = pools.account(pool)
        name = account.name or account.slug or f"Account {account.uid[:8]}"
        owner = pools.owner(pool)
        owned_by = owner_label(owner, user_uid)
        if owner is not None and owner.uid != user_uid and owned_by == name:
            # An organization's account named after it
            owned_by = ORGANIZATION
        notes = [owned_by]
        if per_account[pool.account_uid] > 1:
            notes.append(pool.grant_type)
        labels[pool.grant_uid] = f"{name} ({', '.join(notes)})"
    return labels
