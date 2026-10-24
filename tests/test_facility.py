import json

import pytest
from django.urls import reverse
from tom_observations.models import ObservationRecord
from tom_targets.models import Target

from tom_skynet.facility import SkynetFacility, SkynetSubmitError
from tom_skynet.models import SkynetProfile

from .conftest import load

pytestmark = pytest.mark.django_db

PUBLISH = ("POST", "/v1/observation-specs:publish")


@pytest.fixture
def ada(django_user_model):
    user = django_user_model.objects.create_user(username="ada", password="x" * 16)
    SkynetProfile.objects.create(user=user, api_token="ada-token")
    return user


@pytest.fixture
def target():
    return Target.objects.create(
        name="AT 2026abc", type=Target.SIDEREAL, ra=150.025, dec=-32.4
    )


def form_data(target, **overrides) -> dict:
    """The create view's POST for the contract's SNR request (publish_snr.json)"""
    values = {
        "facility": "Skynet",
        "target_id": target.pk,
        "observation_type": "IMAGING",
        "name": "AT 2026abc follow-up",
        "grant": "99",
        "start": "2026-11-01T00:00",
        "end": "2026-11-08T00:00",
        "filter_1": "r'",
        "target_snr_1": "10",
        "count_1": "3",
        "filter_2": "i'",
        "target_snr_2": "10",
        "count_2": "3",
        "filter_3": "",
        "magnitude": "17.8",
        "magnitude_filter": "r'",
        "measured_at": "2026-10-31T06:00",
        "event_time": "2026-10-31T02:00",
        "decay_index": "1.2",
        "spectral_index": "0.7",
        "max_airmass": "2",
        "submit": "Submit",
    }
    values.update(overrides)
    return values


class TestSubmit:
    @pytest.fixture
    def create_url(self):
        return reverse("tom_observations:create", kwargs={"facility": "Skynet"})

    def test_one_record_with_the_grant_in_its_parameters(
        self, client, ada, target, skynet_api, create_url
    ):
        client.force_login(ada)
        response = client.post(create_url, form_data(target))
        assert response.status_code == 302
        (publish,) = skynet_api.sent(*PUBLISH)
        assert json.loads(publish.content) == load("publish_snr.json")
        assert publish.headers["Authorization"] == "Bearer ada-token"
        (created,) = ObservationRecord.objects.all()
        assert created.observation_id == "310"
        assert created.user == ada
        assert created.parameters["grant"] == 99
        assert created.parameters["start"] == "2026-11-01T00:00:00+00:00"

    def test_validate_does_not_submit(
        self, client, ada, target, skynet_api, create_url
    ):
        client.force_login(ada)
        values = form_data(target, validate="Validate")
        del values["submit"]
        response = client.post(create_url, values)
        assert response.status_code == 200
        assert b"This observation is valid." in response.content
        assert not skynet_api.sent(*PUBLISH)
        assert not ObservationRecord.objects.exists()

    def test_a_refusal_reaches_the_form(
        self, client, ada, target, skynet_api, create_url
    ):
        skynet_api.publish = (
            422,
            {"detail": [{"msg": "Target never rises above airmass 2.0"}]},
        )
        client.force_login(ada)
        response = client.post(create_url, form_data(target))
        assert response.status_code == 200
        assert (
            "Skynet didn&#x27;t accept the request: Target never rises above "
            "airmass 2.0 (HTTP 422)"
        ) in response.content.decode()
        assert not ObservationRecord.objects.exists()

    def test_a_payload_without_the_form(self, ada, target, skynet_api):
        facility = SkynetFacility()
        facility.set_user(ada)
        payload = {"target_id": target.pk, "params": form_data(target)}
        del payload["params"]["submit"]
        assert facility.validate_observation(payload) == []
        assert not skynet_api.sent(*PUBLISH)
        assert facility.submit_observation(payload) == ["310"]
        assert len(skynet_api.sent(*PUBLISH)) == 1

    def test_an_invalid_payload_without_the_form(self, ada, target, skynet_api):
        facility = SkynetFacility()
        facility.set_user(ada)
        payload = {
            "target_id": target.pk,
            "params": form_data(target, target_snr_1="11"),
        }
        assert facility.validate_observation(payload) == [
            "Target SNR: Choose a target SNR from 1.0 to 10.0."
        ]
        with pytest.raises(SkynetSubmitError, match="from 1.0 to 10.0"):
            facility.submit_observation(payload)
        assert not skynet_api.sent(*PUBLISH)
