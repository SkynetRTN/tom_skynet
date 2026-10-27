import io
import json

import numpy as np
import pytest
from astropy.io import fits
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse
from guardian.shortcuts import assign_perm
from tom_dataproducts.models import DataProduct
from tom_observations.models import ObservationRecord
from tom_targets.models import Target

import tom_skynet
from tom_skynet.facility import SkynetFacility, SkynetSubmitError
from tom_skynet.models import SkynetProfile, SkynetRecordState

from .conftest import load

pytestmark = pytest.mark.django_db

PUBLISH = ("POST", "/v1/observation-specs:publish")
PRESIGN = ("POST", "/v1/files/data/presign")
FILE_ID = load("progress_products.json")["products"][0]["fileId"]


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
        assert created.parameters["skynet_owner"] == "Ada Lovelace (user)"
        assert created.parameters["start"] == "2026-11-01T00:00:00+00:00"

    def test_validate_previews_the_telescopes_and_does_not_submit(
        self, client, ada, target, skynet_api, create_url
    ):
        skynet_api.unobservable = {105}
        client.force_login(ada)
        values = form_data(target, validate="Validate")
        del values["submit"]
        response = client.post(create_url, values)
        assert response.status_code == 200
        page = response.content.decode()
        assert (
            "Skynet accepts this request. It can run on 2 of 3 telescopes (Skynet "
            "Simulator — Optical, Karoo, Skynet Simulator — Optical, Outback), with "
            "an estimated 15.5–20 s of exposure in 6 exposures. Not observable in "
            "the window: Skynet Simulator — Optical, Atacama."
        ) in page
        assert len(skynet_api.sent("POST", "/v1/observation-specs:validate")) == 1
        assert not skynet_api.sent(*PUBLISH)
        assert not ObservationRecord.objects.exists()

    def test_validate_shows_skynets_field_errors(
        self, client, ada, target, skynet_api, create_url
    ):
        skynet_api.validate = (422, load("dry_run/validate_error.json"))
        client.force_login(ada)
        values = form_data(target, validate="Validate")
        del values["submit"]
        response = client.post(create_url, values)
        assert (
            "Target SNR: Input should be a valid number, unable to parse string as "
            "a number"
        ) in response.content.decode()
        assert not skynet_api.sent("POST", "/v1/observation-specs:plan")

    def test_submit_refuses_what_no_telescope_can_carry_out(
        self, client, ada, target, skynet_api, create_url
    ):
        skynet_api.incompatible = {105, 106, 107}
        client.force_login(ada)
        response = client.post(create_url, form_data(target))
        assert response.status_code == 200
        assert (
            "no telescope on this time account can carry it out: No optical_imaging "
            "request matches the instrument&#x27;s capabilities."
        ) in response.content.decode()
        assert not skynet_api.sent(*PUBLISH)

    def test_submit_refuses_what_no_telescope_can_observe(
        self, client, ada, target, skynet_api, create_url
    ):
        skynet_api.unobservable = {105, 106, 107}
        client.force_login(ada)
        response = client.post(create_url, form_data(target))
        assert "can observe it in the window" in response.content.decode()
        assert not skynet_api.sent(*PUBLISH)

    def test_a_window_checked_only_in_part_is_submitted(
        self, client, ada, target, skynet_api, create_url
    ):
        skynet_api.unobservable = {105, 106, 107}
        client.force_login(ada)
        response = client.post(create_url, form_data(target, end="2026-12-01T00:00"))
        assert response.status_code == 302
        assert len(skynet_api.sent(*PUBLISH)) == 1

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


class TestWithoutAUser:
    def test_a_payload_without_a_user_is_refused_clearly(self, ada, target, skynet_api):
        payload = {"target_id": target.pk, "params": form_data(target)}
        with pytest.raises(SkynetSubmitError, match="need a TOM user"):
            SkynetFacility().submit_observation(payload)
        assert skynet_api.requests == []

    def test_the_rest_api_is_refused_clearly(self, ada, target, skynet_api):
        from rest_framework.test import APIClient

        assign_perm("tom_targets.view_target", ada, target)
        api = APIClient()
        api.force_authenticate(ada)
        response = api.post(
            "/api/observations/",
            {
                "facility": "Skynet",
                "target_id": target.pk,
                "observation_type": "IMAGING",
                "observing_parameters": form_data(target),
            },
            format="json",
        )
        assert response.status_code == 400
        assert "need a TOM user" in response.content.decode()
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
        assert polls() == 1  # one read for the state and the products to save
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


class TestCancel:
    PATCH = ("PATCH", "/v1/observations/317")

    def cancel(self, client, user, observation):
        client.force_login(user)
        return client.get(
            reverse("tom_observations:cancel", kwargs={"pk": observation.pk}),
            follow=True,
        )

    def test_the_record_page_cancels_as_the_owner(
        self, client, ada, target, skynet_api
    ):
        pending = record(ada, target, status="PENDING")
        skynet_api.progress["317"] = progress("PENDING", id=317)
        response = self.cancel(client, ada, pending)
        assert b"Successfully cancelled" in response.content
        (patch,) = skynet_api.sent(*self.PATCH)
        assert json.loads(patch.content) == {"status": "canceled"}
        assert patch.headers["Authorization"] == "Bearer ada-token"
        pending.refresh_from_db()
        assert pending.status == "CANCELED"

    def test_another_tom_user_cannot_cancel(
        self, client, ada, target, skynet_api, django_user_model
    ):
        pending = record(ada, target, status="PENDING")
        skynet_api.progress["317"] = progress("PENDING", id=317)
        bob = django_user_model.objects.create_user(username="bob")
        response = self.cancel(client, bob, pending)
        assert b"Only the TOM user who submitted it" in response.content
        assert not skynet_api.sent(*self.PATCH)

    def test_staff_can_cancel(self, client, ada, target, skynet_api, django_user_model):
        pending = record(ada, target, status="PENDING")
        skynet_api.progress["317"] = progress("PENDING", id=317)
        admin = django_user_model.objects.create_superuser(username="root")
        self.cancel(client, admin, pending)
        (patch,) = skynet_api.sent(*self.PATCH)
        assert patch.headers["Authorization"] == "Bearer ada-token"

    def test_a_terminal_observation_is_left_alone(self, ada, target, skynet_api):
        record(ada, target, status="COMPLETED")
        skynet_api.progress["317"] = products(state="completed")
        facility = SkynetFacility()
        facility.set_user(ada)
        assert facility.cancel_observation("317") is True
        assert not skynet_api.sent(*self.PATCH)

    def test_a_refusal_reaches_the_page(self, client, ada, target, skynet_api):
        pending = record(ada, target, status="PENDING")
        skynet_api.progress["317"] = progress("PENDING", id=317)
        skynet_api.cancel_status = 409
        response = self.cancel(client, ada, pending)
        assert (
            "Skynet didn&#x27;t cancel it: Observation can&#x27;t be canceled (HTTP 409)"
            in response.content.decode()
        )

    @pytest.mark.parametrize(("override", "offered"), [(False, False), (True, True)])
    def test_a_scheduled_record_offers_cancel_with_the_override(
        self, client, ada, target, skynet_api, settings, override, offered
    ):
        if override:
            settings.TEMPLATES = [
                settings.TEMPLATES[0] | {"DIRS": [tom_skynet.TEMPLATE_OVERRIDES]}
            ]
        scheduled = record(ada, target, status="SCHEDULED")
        assign_perm("tom_targets.view_target", ada, target)
        skynet_api.progress["317"] = progress("SCHEDULED", id=317)
        client.force_login(ada)
        page = client.get(
            reverse("tom_observations:detail", kwargs={"pk": scheduled.pk})
        ).content.decode()
        assert ("Cancel Observation" in page) is offered

    def test_the_rest_api_cancel_is_refused(self, ada, target, skynet_api):
        # It names no user, so it can't tell the owner from anyone who can see
        # the target
        from rest_framework.test import APIClient

        pending = record(ada, target, status="PENDING")
        assign_perm("tom_targets.view_target", ada, target)
        skynet_api.progress["317"] = progress("PENDING", id=317)
        api = APIClient()
        api.force_authenticate(ada)
        response = api.patch(f"/api/observations/{pending.pk}/cancel/")
        assert response.status_code == 400
        assert "observation page" in response.content.decode()
        assert not skynet_api.sent(*self.PATCH)


class TestDataProducts:
    def test_final_products_are_saved_while_polling(self, ada, target, skynet_api):
        polled = record(ada, target)
        skynet_api.progress["317"] = products()
        SkynetFacility().update_observation_status("317")
        (saved,) = DataProduct.objects.all()
        file_id = load("progress_products.json")["products"][0]["fileId"]
        assert saved.product_id == f"skynet:{file_id}"
        assert saved.observation_record == polled
        assert saved.target == target
        assert saved.data.name.endswith("reduced-0001.fits")
        assert skynet_api.downloads == [f"https://minio.invalid/{file_id}"]

    def test_saved_products_are_not_fetched_again(self, ada, target, skynet_api):
        record(ada, target)
        skynet_api.progress["317"] = products()
        facility = SkynetFacility()
        facility.update_observation_status("317")
        facility.update_observation_status("317")
        assert len(skynet_api.sent(*PRESIGN)) == 1
        assert len(skynet_api.downloads) == 1

    def test_a_failed_download_saves_nothing_and_is_retried(
        self, ada, target, skynet_api
    ):
        polled = record(ada, target)
        skynet_api.progress["317"] = products(productsFinal=True, state="completed")
        skynet_api.download_status = 403  # an expired presigned URL
        facility = SkynetFacility()
        assert facility.update_all_observation_statuses() == [
            ("317", f"403 for https://minio.invalid/{FILE_ID}")
        ]
        assert not DataProduct.objects.exists()
        assert not SkynetRecordState.objects.filter(
            record=polled, products_final=True
        ).exists()

        skynet_api.download_status = 200
        assert facility.update_all_observation_statuses() == []
        (saved,) = DataProduct.objects.all()
        assert saved.data
        assert SkynetRecordState.objects.get(record=polled).products_final

    def test_a_product_the_presign_leaves_out_keeps_the_record_polled(
        self, ada, target, skynet_api
    ):
        polled = record(ada, target)
        second = product("second-file", assetId=227, filename="reduced-0002.fits")
        skynet_api.progress["317"] = products(
            state="completed", productsFinal=True, products=[product(FILE_ID), second]
        )
        skynet_api.unsigned = {"second-file"}  # e.g. a signing error
        facility = SkynetFacility()
        assert facility.update_all_observation_statuses() == []
        assert list(DataProduct.objects.values_list("product_id", flat=True)) == [
            f"skynet:{FILE_ID}"
        ]
        assert not SkynetRecordState.objects.get(record=polled).products_final

        skynet_api.unsigned = set()
        assert facility.update_all_observation_statuses() == []
        assert DataProduct.objects.count() == 2
        assert SkynetRecordState.objects.get(record=polled).products_final
        before = len(skynet_api.requests)
        assert facility.update_all_observation_statuses() == []
        assert len(skynet_api.requests) == before  # finished: no longer polled

    def test_an_empty_presign_answer_saves_nothing_and_keeps_polling(
        self, ada, target, skynet_api
    ):
        polled = record(ada, target)
        skynet_api.progress["317"] = products(state="completed", productsFinal=True)
        skynet_api.unsigned = {FILE_ID}
        SkynetFacility().update_observation_status("317")
        assert not DataProduct.objects.exists()
        assert not SkynetRecordState.objects.get(record=polled).products_final

    def test_an_empty_product_left_by_the_base_class_is_filled(
        self, ada, target, skynet_api
    ):
        polled = record(ada, target)
        DataProduct.objects.create(
            product_id=f"skynet:{FILE_ID}", target=target, observation_record=polled
        )
        skynet_api.progress["317"] = products()
        SkynetFacility().update_observation_status("317")
        (saved,) = DataProduct.objects.all()
        assert saved.data.name.endswith("reduced-0001.fits")

    def test_a_frame_still_processing_is_not_saved(self, ada, target, skynet_api):
        record(ada, target)
        raw = product("raw-file", isFinal=False, isReduced=False)
        skynet_api.progress["317"] = products(products=[raw])
        SkynetFacility().update_observation_status("317")
        assert not DataProduct.objects.exists()
        assert not skynet_api.sent(*PRESIGN)

    def test_a_reprocessed_frame_is_a_second_product(self, ada, target, skynet_api):
        record(ada, target)
        skynet_api.progress["317"] = products()
        facility = SkynetFacility()
        facility.update_observation_status("317")
        rerun = product("reprocessed-file", assetId=227, filename="reduced-0002.fits")
        skynet_api.progress["317"] = products(products=[rerun])
        facility.update_observation_status("317")
        assert sorted(DataProduct.objects.values_list("product_id", flat=True)) == [
            f"skynet:{load('progress_products.json')['products'][0]['fileId']}",
            "skynet:reprocessed-file",
        ]

    def test_the_products_offered_by_id(self, ada, target, skynet_api):
        record(ada, target)
        skynet_api.progress["317"] = products(
            products=[
                product("a"),
                product("b", takenAtStartUtc="2026-10-23T13:40:00Z"),
            ]
        )
        offered = SkynetFacility().data_products("317")
        assert [p["id"] for p in offered] == ["skynet:a", "skynet:b"]
        assert offered[1]["created"].isoformat() == "2026-10-23T13:40:00+00:00"
        assert offered[0]["url"] == "https://minio.invalid/a"
        assert SkynetFacility().data_products("317", "skynet:b") == [offered[1]]

    def test_the_record_page_lists_the_unsaved_products(
        self, client, ada, target, skynet_api
    ):
        page = record(ada, target)
        skynet_api.progress["317"] = products()
        assign_perm("tom_targets.view_target", ada, target)
        client.force_login(ada)
        response = client.get(reverse("tom_observations:detail", args=[page.pk]))
        assert response.status_code == 200
        assert len(response.context["data_products"]["unsaved"]) == 1

    def test_the_record_page_without_a_token(
        self, client, target, skynet_api, django_user_model
    ):
        bob = django_user_model.objects.create_user(username="bob")
        page = record(bob, target)
        assign_perm("tom_targets.view_target", bob, target)
        client.force_login(bob)
        response = client.get(reverse("tom_observations:detail", args=[page.pk]))
        assert response.status_code == 200
        assert response.context["data_products"] == {"saved": [], "unsaved": []}


class TestProductMetadata:
    def test_a_saved_product_is_a_fits_file_with_its_metadata(
        self, ada, target, skynet_api
    ):
        record(ada, target)
        skynet_api.progress["317"] = products()
        # The products' telescope (recorded elsewhere) among the owner's pools
        expected = load("progress_products.json")["products"][0]
        telescope = skynet_api.pools["telescopes"]["105"] | {
            "id": expected["telescopeId"]
        }
        skynet_api.pools["telescopes"][str(expected["telescopeId"])] = telescope
        SkynetFacility().update_observation_status("317")
        (saved,) = DataProduct.objects.all()
        assert saved.data_product_type == "fits_file"
        metadata = json.loads(saved.extra_data)
        assert metadata["skynet_file_id"] == FILE_ID
        assert metadata["filter_id"] == expected["filterId"]
        assert metadata["telescope_id"] == expected["telescopeId"]
        assert metadata["is_reduced"] is True
        assert metadata["telescope_slug"] == "sim-optical-atacama"

    def test_a_telescope_the_pools_dont_list_is_left_out(self, ada, target, skynet_api):
        record(ada, target)
        skynet_api.progress["317"] = products()
        SkynetFacility().update_observation_status("317")
        metadata = json.loads(DataProduct.objects.get().extra_data)
        assert "telescope_slug" not in metadata and metadata["telescope_id"]

    def test_no_type_when_the_tom_lacks_fits_file(
        self, ada, target, skynet_api, monkeypatch
    ):
        monkeypatch.setattr(
            "tom_dataproducts.models.DATA_TYPE_CHOICES",
            (("photometry", "Photometry"),),
        )
        record(ada, target)
        skynet_api.progress["317"] = products()
        SkynetFacility().update_observation_status("317")
        assert DataProduct.objects.get().data_product_type == ""

    def test_an_unsigned_product_has_no_link(self, ada, target, skynet_api):
        record(ada, target)
        skynet_api.progress["317"] = products()
        skynet_api.unsigned = {FILE_ID}
        (offered,) = SkynetFacility().data_products("317")
        assert offered["url"] is None and offered["data"] == ""

    def test_unsaved_products_link_their_url(self, ada, target, skynet_api):
        record(ada, target)
        skynet_api.progress["317"] = products()
        (offered,) = SkynetFacility().data_products("317")
        assert offered["data"] == offered["url"] == f"https://minio.invalid/{FILE_ID}"


class TestSites:
    def test_none_by_default(self, skynet_api):
        assert SkynetFacility().get_observing_sites() == {}

    def test_from_the_settings(self, settings):
        sites = {
            "Karoo": {
                "sitecode": "sim-optical-karoo",
                "latitude": -31.3,
                "longitude": 21.8,
                "elevation": 1300,
            }
        }
        settings.FACILITIES = {"SKYNET": {"sites": sites}}
        assert SkynetFacility().get_observing_sites() == sites


def test_the_observation_link_needs_no_token(skynet_api):
    assert (
        SkynetFacility().get_observation_url("317")
        == "http://web.test/observations/317"
    )


def skynet_frame() -> bytes:
    """A frame as Skynet stores it: tile-compressed, no EXTNAME on the image"""
    rng = np.random.default_rng(317)
    data = rng.normal(1000, 30, size=(300, 400)).astype(np.float32)
    buffer = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), fits.CompImageHDU(data)]).writeto(buffer)
    return buffer.getvalue()


class TestThumbnails:
    def test_tom_toolkit_draws_none_for_a_skynet_frame(self, ada, target, skynet_api):
        saved = DataProduct.objects.create(product_id="skynet:x", target=target)
        saved.data.save("frame.fits", ContentFile(skynet_frame()))
        assert saved.create_thumbnail(width=200, height=200) is None

    def test_a_saved_frame_has_a_thumbnail(self, ada, target, skynet_api):
        record(ada, target)
        skynet_api.progress["317"] = products()
        skynet_api.download_content = skynet_frame()
        SkynetFacility().update_observation_status("317")
        saved = DataProduct.objects.get()
        assert saved.thumbnail.name.endswith("reduced-0001_tb.jpg")
        # TOM Toolkit's page shows it as is, without trying to redraw it
        assert saved.get_preview() == saved.thumbnail.url
        saved.thumbnail.open("rb")
        assert saved.thumbnail.read(2) == b"\xff\xd8"  # a JPEG

    def test_a_file_it_cant_draw_is_saved_without_one(self, ada, target, skynet_api):
        record(ada, target)
        skynet_api.progress["317"] = products()
        SkynetFacility().update_observation_status("317")
        saved = DataProduct.objects.get()
        assert saved.data and not saved.thumbnail

    def test_the_command_draws_the_missing_ones(self, ada, target, skynet_api, capsys):
        older = DataProduct.objects.create(product_id="skynet:old", target=target)
        older.data.save("old.fits", ContentFile(skynet_frame()))
        other = DataProduct.objects.create(product_id="lco:1", target=target)
        other.data.save("lco.fits", ContentFile(skynet_frame()))
        call_command("skynet_thumbnails")
        older.refresh_from_db()
        other.refresh_from_db()
        assert older.thumbnail.name.endswith("old_tb.jpg")
        assert not other.thumbnail  # not Skynet's
        call_command("skynet_thumbnails")
        assert "Drew 0 thumbnails" in capsys.readouterr().out.splitlines()[-1]

    def test_a_redraw_replaces_the_thumbnail(self, ada, target, skynet_api, capsys):
        older = DataProduct.objects.create(product_id="skynet:old", target=target)
        older.data.save("old.fits", ContentFile(skynet_frame()))
        call_command("skynet_thumbnails")
        older.refresh_from_db()
        first = older.thumbnail.path
        call_command("skynet_thumbnails", "--redraw")
        older.refresh_from_db()
        assert "Drew 1 thumbnails" in capsys.readouterr().out.splitlines()[-1]
        assert older.thumbnail.path == first  # the old file went first


class TestLostAccess:
    def test_a_404_marks_the_record_and_stops_polling_it(self, ada, target, skynet_api):
        lost = record(ada, target)
        # Not in skynet_api.progress: Skynet answers 404, as for one Ada can't view
        facility = SkynetFacility()
        assert facility.update_all_observation_statuses() == []
        assert SkynetRecordState.objects.get(record=lost).access_lost
        polls = len(skynet_api.requests)
        facility.update_all_observation_statuses()
        assert len(skynet_api.requests) == polls

    def test_the_record_page_says_so(self, client, ada, target, skynet_api, settings):
        lost = record(ada, target)
        SkynetFacility().update_observation_status("317")
        settings.TEMPLATES = [
            settings.TEMPLATES[0] | {"DIRS": [tom_skynet.TEMPLATE_OVERRIDES]}
        ]
        assign_perm("tom_targets.view_target", ada, target)
        client.force_login(ada)
        polls = len(skynet_api.requests)
        page = client.get(reverse("tom_observations:detail", args=[lost.pk]))
        assert "no longer lets this record" in page.content.decode()
        assert len(skynet_api.requests) == polls  # nor asks Skynet for products

    def test_other_errors_still_fail_the_poll(self, ada, target, skynet_api):
        record(ada, target)
        skynet_api.rejected_tokens = {"ada-token"}
        (failed,) = SkynetFacility().update_all_observation_statuses()
        assert failed[0] == "317"
        assert not SkynetRecordState.objects.filter(access_lost=True).exists()
