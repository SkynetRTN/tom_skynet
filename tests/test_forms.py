import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from aeonlib.exceptions import AuthenticationError
from aeonlib.skynet import InstrumentPools
from crispy_forms.utils import render_crispy_form
from tom_targets.models import Target

from tom_skynet.credentials import SkynetCredentialsError
from tom_skynet.facility import SkynetFacility
from tom_skynet.forms import SkynetImagingForm
from tom_skynet.pools import pool_labels

DATA = Path(__file__).parent / "data"

pytestmark = pytest.mark.django_db


def load(name: str):
    return json.loads((DATA / name).read_text())


class FakeSkynet:
    def __init__(self, pools):
        self.pools = pools

    def instrument_pools(self):
        return self.pools


@pytest.fixture
def pools():
    return InstrumentPools.model_validate(load("instrument_pools.json"))


@pytest.fixture
def skynet(monkeypatch, pools):
    """Make every form see these pools instead of calling Skynet"""
    fake = FakeSkynet(pools)
    monkeypatch.setattr("tom_skynet.forms.facility_for_user", lambda user: fake)
    return fake


@pytest.fixture
def facility(django_user_model):
    facility = SkynetFacility()
    facility.set_user(django_user_model.objects.create_user(username="ada"))
    return facility


@pytest.fixture
def target():
    return Target.objects.create(
        name="AT 2026abc", type=Target.SIDEREAL, ra=150.025, dec=-32.4
    )


def data(target, **overrides) -> dict:
    """The form data for the contract's SNR request (publish_snr.json)"""
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
    }
    values.update(overrides)
    return values


def messages(bound: SkynetImagingForm, field: str) -> str:
    """A field's error messages as plain text (str(errors) is HTML-escaped)"""
    return " ".join(e["message"] for e in bound.errors.get_json_data()[field])


def form(facility, values=None, **initial) -> SkynetImagingForm:
    # The create view always passes the target in the initial data; the Back
    # button links to it.
    initial.setdefault("target_id", 1)
    return SkynetImagingForm(data=values, initial=initial, facility=facility)


class TestChoices:
    def test_every_pool_without_a_guard(self, skynet, facility):
        assert form(facility).choice_field("grant").choices == [
            (99, "Ada's account"),
            (100, "Transient team"),
        ]

    def test_the_guard_hides_pools_reaching_other_telescopes(
        self, skynet, facility, settings
    ):
        settings.FACILITIES = {"SKYNET": {"allowed_telescope_prefix": "sim-"}}
        # Grant 100 also reaches PROMPT-CTIO-6, so only the sim-only pool is left.
        assert form(facility).choice_field("grant").choices == [(99, "Ada's account")]

    def test_several_grants_on_one_account_name_their_type(self, pools):
        pools.pools[1].account_id = pools.pools[0].account_id
        assert pool_labels(pools, pools.pools) == {
            99: "Ada's account (owner)",
            100: "Ada's account (member)",
        }

    def test_filters_are_the_union_of_the_shown_pools(self, skynet, facility, pools):
        first = cast(list, form(facility).choice_field("filter_1").choices)
        assert [value for value, _ in first] == sorted(pools.filters)
        later = cast(list, form(facility).choice_field("filter_2").choices)
        assert later[0] == ("", "---------")

    def test_the_layout_renders_every_row(self, skynet, facility):
        html = render_crispy_form(form(facility))
        for name in ("grant", "filter_3", "target_snr_3", "count_3", "spectral_index"):
            assert f'name="{name}"' in html


class TestValidRequest:
    def test_builds_the_contract_publish_body(self, skynet, facility, target):
        bound = form(facility, data(target))
        assert bound.is_valid(), bound.errors
        assert bound.skynet_request is not None
        body = bound.skynet_request.to_publish_body(owner_id=133)
        assert body == load("publish_snr.json")

    def test_parameters_are_json_safe(self, skynet, facility, target):
        bound = form(facility, data(target))
        assert bound.is_valid(), bound.errors
        parameters = bound.serialize_parameters()
        json.dumps(parameters)
        assert parameters["start"] == "2026-11-01T00:00:00+00:00"
        assert parameters["grant"] == 99

    def test_unused_filter_rows_are_ignored(self, skynet, facility, target):
        values = data(target, filter_2="", target_snr_2="", count_2="")
        bound = form(facility, values)
        assert bound.is_valid(), bound.errors
        assert bound.skynet_request is not None
        assert [e.filter_id for e in bound.skynet_request.exposures] == ["r'"]

    def test_unbound_form_offers_a_week_from_now(self, skynet, facility):
        unbound = form(facility)
        assert (unbound.initial["end"] - unbound.initial["start"]).days == 7

    def test_times_are_shown_in_utc(self, skynet, facility):
        # The test TOM's TIME_ZONE is New York; the form still shows UTC.
        start = datetime(2026, 11, 1, tzinfo=UTC)
        unbound = form(facility, start=start)
        assert 'value="2026-11-01T00:00"' in str(unbound["start"])


class TestInvalidRequest:
    def test_the_window_must_be_open(self, skynet, facility, target):
        bound = form(facility, data(target, end="2026-11-01T00:00"))
        assert not bound.is_valid()
        assert "window.start must be before window.end" in str(bound.non_field_errors())

    def test_the_event_must_come_before_the_window(self, skynet, facility, target):
        values = data(
            target, event_time="2026-11-02T00:00", measured_at="2026-11-03T00:00"
        )
        bound = form(facility, values)
        assert not bound.is_valid()
        assert "event_time must be before window.start" in str(bound.non_field_errors())

    def test_a_filter_the_account_lacks(self, skynet, facility, target, pools):
        del pools.pools[1].filter_instrument_ids["r'"]
        bound = form(facility, data(target, grant="100"))
        assert not bound.is_valid()
        assert "isn't offered" in messages(bound, "filter_1")

    def test_snr_outside_the_accounts_limits(self, skynet, facility, target):
        bound = form(facility, data(target, target_snr_1="11"))
        assert not bound.is_valid()
        assert "from 1.0 to 10.0" in messages(bound, "target_snr_1")

    def test_field_errors_are_repeated_where_the_tom_shows_them(
        self, skynet, facility, target
    ):
        # The create view only displays non-field errors after a failed submit.
        bound = form(facility, data(target, target_snr_1="11"))
        assert not bound.is_valid()
        assert "Target SNR: Choose a target SNR from 1.0 to 10.0." in " ".join(
            str(e) for e in bound.non_field_errors()
        )
        assert messages(bound, "target_snr_1")

    def test_a_filter_row_needs_its_snr_and_count(self, skynet, facility, target):
        bound = form(facility, data(target, filter_3="V"))
        assert not bound.is_valid()
        assert set(bound.errors) >= {"target_snr_3", "count_3"}

    def test_an_unknown_time_account(self, skynet, facility, target):
        bound = form(facility, data(target, grant="12345"))
        assert not bound.is_valid()
        assert "grant" in bound.errors

    def test_a_non_sidereal_target(self, skynet, facility):
        comet = Target.objects.create(name="C/2026 A1", type=Target.NON_SIDEREAL)
        bound = form(facility, data(comet))
        assert not bound.is_valid()
        assert "sidereal targets" in str(bound.non_field_errors())


class TestUnavailable:
    def test_no_token(self, facility, monkeypatch, target):
        def no_token(user):
            raise SkynetCredentialsError("No Skynet API token for ada")

        monkeypatch.setattr("tom_skynet.forms.facility_for_user", no_token)
        bound = form(facility, data(target))
        assert not bound.is_valid()
        assert "Set your Skynet API token first" in str(bound.non_field_errors())
        assert "Set your Skynet API token first" in render_crispy_form(form(facility))

    def test_skynet_refuses_the_token(self, facility, monkeypatch):
        class Refusing:
            def instrument_pools(self):
                raise AuthenticationError("Skynet: <b>insufficient_scope</b>")

        monkeypatch.setattr(
            "tom_skynet.forms.facility_for_user", lambda user: Refusing()
        )
        unbound = form(facility)
        assert unbound.unavailable.startswith("Skynet is unavailable")
        html = render_crispy_form(unbound)
        assert "&lt;b&gt;insufficient_scope&lt;/b&gt;" in html
        assert "<b>insufficient_scope</b>" not in html

    def test_no_usable_account(self, skynet, facility, settings):
        settings.FACILITIES = {"SKYNET": {"allowed_telescope_prefix": "nowhere-"}}
        assert "None of your Skynet time accounts" in form(facility).unavailable


class TestFacilityForms:
    def test_the_facility_offers_the_imaging_form(self):
        facility = SkynetFacility()
        assert facility.get_form("IMAGING") is SkynetImagingForm
        assert facility.get_form_classes_for_display() == {"IMAGING": SkynetImagingForm}
