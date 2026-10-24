import json

import pytest
from django.core.management import call_command
from django.urls import reverse
from tom_dataproducts.models import DataProduct
from tom_observations.models import ObservationRecord
from tom_targets.models import Target

from tom_skynet.facility import SkynetFacility, SkynetSubmitError
from tom_skynet.models import SkynetProfile, SkynetRecordState

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


def record(owner, target, observation_id="317", status=""):
    return ObservationRecord.objects.create(
        target=target,
        user=owner,
        facility="Skynet",
        observation_id=observation_id,
        status=status,
        parameters={},
    )


def progress(state="IN_PROGRESS", **changes) -> dict:
    return load(f"progress/{state}.json") | changes


def products(**changes) -> dict:
    """progress_products.json: observation 317, one final reduced product"""
    return load("progress_products.json") | changes


def product(file_id: str, **changes) -> dict:
    return load("progress_products.json")["products"][0] | {"fileId": file_id} | changes


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


class TestStatus:
    def test_updatestatus_needs_no_username(self, ada, target, skynet_api):
        record(ada, target, "311")
        skynet_api.progress["311"] = progress("SCHEDULED", id=311)
        call_command("updatestatus")
        updated = ObservationRecord.objects.get()
        assert updated.status == "SCHEDULED"
        assert updated.scheduled_start is not None
        (request,) = skynet_api.requests
        assert request.headers["Authorization"] == "Bearer ada-token"

    def test_the_record_owners_token_whoever_is_set(
        self, ada, target, skynet_api, django_user_model
    ):
        record(ada, target, "311")
        skynet_api.progress["311"] = progress("SCHEDULED", id=311)
        facility = SkynetFacility()
        facility.set_user(django_user_model.objects.create_user(username="bob"))
        assert facility.get_observation_status("311")["state"] == "SCHEDULED"
        (request,) = skynet_api.requests
        assert request.headers["Authorization"] == "Bearer ada-token"

    def test_a_record_without_a_token_fails_alone(
        self, ada, target, skynet_api, django_user_model
    ):
        bob = django_user_model.objects.create_user(username="bob")
        record(bob, target, "300")
        record(ada, target, "311")
        skynet_api.progress["311"] = progress("SCHEDULED", id=311)
        failed = SkynetFacility().update_all_observation_statuses()
        assert failed == [("300", "No Skynet API token for bob")]
        assert ObservationRecord.objects.get(observation_id="311").status == "SCHEDULED"

    def test_a_failed_state(self, ada, target, skynet_api):
        expired = record(ada, target, "315")
        skynet_api.progress["315"] = progress("WINDOW_EXPIRED", id=315)
        SkynetFacility().update_observation_status("315")
        expired.refresh_from_db()
        assert expired.status == "WINDOW_EXPIRED"
        assert expired.terminal and expired.failed

    def test_completed_is_polled_until_its_products_are_final(
        self, ada, target, skynet_api
    ):
        polled = record(ada, target)
        facility = SkynetFacility()

        def polls() -> int:
            before = len(skynet_api.sent("GET", "/v1/observations/317/progress"))
            assert facility.update_all_observation_statuses() == []
            return len(skynet_api.sent("GET", "/v1/observations/317/progress")) - before

        skynet_api.progress["317"] = products(state="completed")
        assert polls() == 2  # the state, then the products to save
        polled.refresh_from_db()
        assert polled.status == "COMPLETED"
        assert DataProduct.objects.count() == 1
        assert not SkynetRecordState.objects.get(record=polled).products_final

        assert polls() == 1  # terminal, but still polled

        skynet_api.progress["317"] = products(state="completed", productsFinal=True)
        assert polls() == 1
        assert SkynetRecordState.objects.get(record=polled).products_final
        assert polls() == 0

    def test_canceled_with_nothing_stops_after_one_poll(self, ada, target, skynet_api):
        record(ada, target, "314")
        skynet_api.progress["314"] = progress("CANCELED")
        facility = SkynetFacility()
        assert facility.update_all_observation_statuses() == []
        assert facility.update_all_observation_statuses() == []
        assert len(skynet_api.requests) == 1
