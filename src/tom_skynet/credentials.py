from aeonlib.conf import Settings
from django.conf import settings as django_settings
from tom_observations.models import ObservationRecord

from .models import SkynetProfile

FACILITY_NAME = "Skynet"


class SkynetCredentialsError(Exception):
    """Raised when no Skynet API token is available for a TOM user"""


def _facility_settings() -> dict:
    return getattr(django_settings, "FACILITIES", {}).get("SKYNET", {})


def web_root() -> str:
    """The Skynet website, from FACILITIES["SKYNET"]["web_root"] or AEONlib's default"""
    return (
        _facility_settings().get("web_root")
        or Settings.model_fields["skynet_web_root"].default
    )


def settings_for_user(user) -> Settings:
    """
    Build AEONlib settings for a TOM user from their Skynet profile. A token in
    FACILITIES["SKYNET"]["api_token"] is used only when the user has none.
    """
    token = None
    if user is not None and user.is_authenticated:
        profile = SkynetProfile.objects.filter(user=user).first()
        token = profile.api_token if profile is not None else None
    facility = _facility_settings()
    token = token or facility.get("api_token")
    if not token:
        name = user.get_username() if user is not None else "an anonymous user"
        raise SkynetCredentialsError(f"No Skynet API token for {name}")
    return Settings(
        skynet_token=token,
        skynet_api_root=facility.get("api_root")
        or Settings.model_fields["skynet_api_root"].default,
        skynet_web_root=web_root(),
    )


def settings_for_observation(observation_id) -> Settings:
    """Build AEONlib settings from the token of the user who owns the observation record"""
    record = (
        ObservationRecord.objects.filter(
            facility=FACILITY_NAME, observation_id=str(observation_id)
        )
        .select_related("user")
        .first()
    )
    if record is None:
        raise SkynetCredentialsError(
            f"No Skynet observation record {observation_id} in this TOM"
        )
    if record.user is None:
        raise SkynetCredentialsError(
            f"Skynet observation {observation_id} has no owner in this TOM"
        )
    return settings_for_user(record.user)
