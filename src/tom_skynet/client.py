from aeonlib.skynet import SkynetFacility as AeonlibSkynetFacility

from .credentials import settings_for_observation, settings_for_user


def facility_for_user(user) -> AeonlibSkynetFacility:
    """An AEONlib Skynet facility acting as a TOM user"""
    return AeonlibSkynetFacility(settings=settings_for_user(user))


def facility_for_observation(observation_id) -> AeonlibSkynetFacility:
    """An AEONlib Skynet facility acting as the owner of an observation record"""
    return AeonlibSkynetFacility(settings=settings_for_observation(observation_id))
