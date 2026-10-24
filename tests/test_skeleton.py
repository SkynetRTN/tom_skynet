from django.apps import apps
from tom_observations.facility import get_service_class, get_service_classes

from tom_skynet.facility import SkynetFacility


def test_the_app_registers_the_facility():
    config = apps.get_app_config("tom_skynet")
    assert config.observation_facilities() == [
        {"class": "tom_skynet.facility.SkynetFacility"}
    ]


def test_the_tom_finds_the_facility_without_settings():
    # TOM_FACILITY_CLASSES is empty: the AppConfig hook alone registers it.
    assert get_service_classes()["Skynet"] is SkynetFacility
    assert get_service_class("Skynet") is SkynetFacility


def test_the_facility_can_be_instantiated():
    facility = SkynetFacility()
    assert facility.get_terminal_observing_states() == [
        "COMPLETED",
        "PARTIALLY_COMPLETED",
        "CANCELED",
        "WINDOW_EXPIRED",
    ]
    assert facility.get_failed_observing_states() == ["WINDOW_EXPIRED"]
    assert facility.get_observing_sites() == {}


def test_aeonlib_brings_the_skynet_facility():
    from aeonlib.skynet import SkynetFacility as AeonlibSkynetFacility

    assert AeonlibSkynetFacility.__module__ == "aeonlib.skynet.facility"
