from aeonlib.skynet import SkynetError
from aeonlib.skynet import SkynetFacility as AeonlibSkynetFacility

from .credentials import settings_for_observation, settings_for_user


def facility_for_user(user) -> AeonlibSkynetFacility:
    """An AEONlib Skynet facility acting as a TOM user"""
    return AeonlibSkynetFacility(settings=settings_for_user(user))


def facility_for_observation(observation_id) -> AeonlibSkynetFacility:
    """An AEONlib Skynet facility acting as the owner of an observation record"""
    return AeonlibSkynetFacility(settings=settings_for_observation(observation_id))


def error_message(exc: Exception) -> str:
    """An AEONlib error as one line for a TOM user"""
    if isinstance(exc, SkynetError):
        detail = exc.detail
        if isinstance(detail, list):
            # FastAPI's validation errors
            detail = "; ".join(
                str(item.get("msg", item)) if isinstance(item, dict) else str(item)
                for item in detail
            )
        return f"{detail} (HTTP {exc.status_code})"
    return str(exc)
