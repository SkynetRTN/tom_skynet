from aeonlib.exceptions import AuthenticationError, ServiceNetworkError
from aeonlib.skynet import SkynetError

from .client import error_message, facility_for_user
from .credentials import SkynetCredentialsError
from .models import SkynetProfile
from .pools import pool_labels, visible_pools


def profile_card(user) -> dict:
    """The Skynet card on the TOM's user profile page"""
    profile = SkynetProfile.objects.filter(user=user).first()
    context: dict = {
        "has_token": bool(profile and profile.api_token),
        "updated_at": profile.updated_at if profile else None,
        "accounts": [],
        "problem": "",
    }
    if not context["has_token"]:
        return context
    try:
        pools = facility_for_user(user).instrument_pools()
    except AuthenticationError:
        context["problem"] = "Skynet didn't accept this token."
        return context
    except (SkynetCredentialsError, ServiceNetworkError, SkynetError) as exc:
        context["problem"] = f"Skynet is unavailable: {error_message(exc)}"
        return context
    shown = visible_pools(pools)
    context["accounts"] = sorted(pool_labels(pools, shown).values())
    return context
