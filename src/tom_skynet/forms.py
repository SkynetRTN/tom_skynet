from datetime import UTC, datetime, timedelta
from typing import cast

from aeonlib.exceptions import AuthenticationError, ServiceNetworkError
from aeonlib.models import SiderealTarget, Window
from aeonlib.skynet import (
    FilterExposure,
    InstrumentPools,
    SkynetError,
    SkynetImagingRequest,
    SourceModel,
    SubmittedObservation,
)
from crispy_forms.layout import HTML, Column, Div, Fieldset, Layout, Row
from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
from django.utils import timezone
from django.utils.html import escape
from pydantic import ValidationError
from tom_observations.facility import BaseRoboticObservationForm
from tom_targets.models import Target

from .client import error_message, facility_for_user
from .credentials import SkynetCredentialsError
from .pools import pool_labels, visible_pools

FILTER_ROWS = 3


class UTCDateTimeField(forms.DateTimeField):
    """A datetime entered and shown in UTC, whatever the TOM's TIME_ZONE"""

    # The format an HTML datetime-local input accepts as its value.
    widget = forms.DateTimeInput(
        format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}
    )

    def to_python(self, value):
        with timezone.override(UTC):
            return super().to_python(value)

    def prepare_value(self, value):
        with timezone.override(UTC):
            return super().prepare_value(value)


def _row_fields(row: int) -> tuple[str, str, str]:
    return f"filter_{row}", f"target_snr_{row}", f"count_{row}"


class SkynetImagingForm(BaseRoboticObservationForm):
    """
    An optical imaging request sized by target SNR; Skynet chooses the telescope.

    A valid form submits itself to Skynet unless the Validate button was pressed:
    TOM Toolkit's create view doesn't catch errors from submit_observation, so
    submitting here is what lets a refusal reach the page as a form error.
    """

    name = forms.CharField(max_length=256)
    grant = forms.TypedChoiceField(coerce=int, label="Time account")
    start = UTCDateTimeField(help_text="UTC")
    end = UTCDateTimeField(help_text="UTC")

    magnitude = forms.FloatField(label="Reference magnitude (AB)")
    magnitude_filter = forms.ChoiceField(label="Reference filter")
    measured_at = UTCDateTimeField(label="Measured at", help_text="UTC")
    event_time = UTCDateTimeField(label="Event time (T0)", help_text="UTC")
    decay_index = forms.FloatField(label="Temporal index (alpha)")
    spectral_index = forms.FloatField(label="Spectral index (beta)")

    max_airmass = forms.FloatField(required=False, min_value=1.0, initial=2.0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for row in range(1, FILTER_ROWS + 1):
            filter_field, snr_field, count_field = _row_fields(row)
            required = row == 1
            self.fields[filter_field] = forms.ChoiceField(
                required=required, label="Filter"
            )
            self.fields[snr_field] = forms.FloatField(
                required=required, min_value=0.0, label="Target SNR"
            )
            self.fields[count_field] = forms.IntegerField(
                required=required, min_value=1, initial=1, label="Count"
            )

        self.skynet = None
        self.pools: InstrumentPools | None = None
        self.unavailable = ""
        self.skynet_request: SkynetImagingRequest | None = None
        self.submitted: SubmittedObservation | None = None
        self._load_pools()
        if not self.is_bound:
            start = datetime.now(UTC).replace(second=0, microsecond=0)
            self.initial.setdefault("start", start)
            self.initial.setdefault("end", start + timedelta(days=7))
        self.helper.layout = Layout(
            self.common_layout, self.layout(), self.button_layout()
        )

    def choice_field(self, name: str) -> forms.ChoiceField:
        return cast(forms.ChoiceField, self.fields[name])

    def _load_pools(self) -> None:
        user = getattr(getattr(self, "facility", None), "user", None)
        try:
            self.skynet = facility_for_user(user)
            self.pools = self.skynet.instrument_pools()
        except SkynetCredentialsError as exc:
            self.unavailable = f"{exc}. Set your Skynet API token first."
            return
        except (AuthenticationError, ServiceNetworkError, SkynetError) as exc:
            self.unavailable = f"Skynet is unavailable: {error_message(exc)}"
            return

        shown = visible_pools(self.pools)
        labels = pool_labels(self.pools, shown)
        self.choice_field("grant").choices = [
            (pool.grant_id, labels[pool.grant_id]) for pool in shown
        ]
        filters = sorted({f for pool in shown for f in pool.filter_instrument_ids})
        choices = [(f, f) for f in filters]
        self.choice_field("magnitude_filter").choices = choices
        self.choice_field("filter_1").choices = choices
        for row in range(2, FILTER_ROWS + 1):
            self.choice_field(f"filter_{row}").choices = [("", "---------"), *choices]
        if not shown:
            self.unavailable = (
                "None of your Skynet time accounts can be used from this TOM."
            )

    def layout(self):
        if not hasattr(self, "pools"):
            # The base form builds a layout before the dynamic fields exist.
            return Layout()
        rows = [
            Row(*(Column(name) for name in _row_fields(row)))
            for row in range(1, FILTER_ROWS + 1)
        ]
        alert = (
            HTML(f'<div class="alert alert-warning">{escape(self.unavailable)}</div>')
            if self.unavailable
            else HTML("")
        )
        return Layout(
            alert,
            Fieldset("Request", "name", "grant", Row(Column("start"), Column("end"))),
            Fieldset("Exposures", *rows),
            Fieldset(
                "Source brightness",
                Row(Column("magnitude"), Column("magnitude_filter")),
                Row(Column("measured_at"), Column("event_time")),
                Row(Column("decay_index"), Column("spectral_index")),
            ),
            Fieldset("Constraints", Div("max_airmass")),
        )

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        if self.unavailable:
            raise forms.ValidationError(self.unavailable)
        if self.errors:
            return cleaned

        exposures = []
        for row in range(1, FILTER_ROWS + 1):
            filter_field, snr_field, count_field = _row_fields(row)
            filter_id = cleaned.get(filter_field)
            if not filter_id:
                continue
            for field in (snr_field, count_field):
                if cleaned.get(field) is None:
                    self.add_error(field, "Required when a filter is chosen.")
            exposures.append((row, filter_id))
        if self.errors:
            return cleaned

        assert self.pools is not None
        pool = self.pools.pool(cleaned["grant"])
        for row, filter_id in exposures:
            if filter_id not in pool.filter_instrument_ids:
                self.add_error(
                    f"filter_{row}",
                    f"{filter_id} isn't offered by this time account's telescopes.",
                )
            snr = cleaned[f"target_snr_{row}"]
            low, high = pool.limits.min_target_snr, pool.limits.max_target_snr
            if (low is not None and snr < low) or (high is not None and snr > high):
                self.add_error(
                    f"target_snr_{row}", f"Choose a target SNR from {low} to {high}."
                )
        if self.errors:
            return cleaned

        target = Target.objects.get(pk=cleaned["target_id"])
        try:
            self.skynet_request = build_request(cleaned, target)
        except ValidationError as exc:
            for error in exc.errors():
                self.add_error(None, error["msg"].removeprefix("Value error, "))
        except ValueError as exc:
            self.add_error(None, str(exc))
        return cleaned

    def full_clean(self):
        super().full_clean()
        # TOM Toolkit's create view shows only non-field errors after a failed
        # submit (it rebuilds the tab forms from the posted values), so repeat
        # each field error there, labelled, or it would never be seen.
        if not self.is_bound:
            return
        field_errors = [
            (name, list(errors))
            for name, errors in self.errors.items()
            if name != NON_FIELD_ERRORS and name in self.fields
        ]
        for name, errors in field_errors:
            label = self.fields[name].label or name.replace("_", " ").capitalize()
            for message in errors:
                self.add_error(None, f"{label}: {message}")

    def is_valid(self):
        valid = super().is_valid()
        if valid and self.submitted is None and "validate" not in self.data:
            self._submit()
        return not self.errors

    def _submit(self) -> None:
        assert self.skynet is not None and self.skynet_request is not None
        try:
            self.submitted = self.skynet.submit(self.skynet_request)
        except (
            ValueError,
            SkynetError,
            AuthenticationError,
            ServiceNetworkError,
        ) as exc:
            self.add_error(
                None, f"Skynet didn't accept the request: {error_message(exc)}"
            )

    def observation_payload(self) -> dict:
        payload = super().observation_payload()
        if self.submitted is not None:
            payload["skynet_observation_id"] = self.submitted.id
        return payload

    def serialize_parameters(self) -> dict:
        """The cleaned data in a JSON-safe form, for ObservationRecord.parameters"""
        parameters = super().serialize_parameters()
        return {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in parameters.items()
            if key != "request"
        }


def build_request(cleaned: dict, target: Target) -> SkynetImagingRequest:
    """Translate the form's cleaned data and its TOM target into an AEONlib request"""
    if target.type != Target.SIDEREAL or target.ra is None or target.dec is None:
        raise ValueError("Skynet observes sidereal targets with RA and Dec only.")
    exposures = []
    for row in range(1, FILTER_ROWS + 1):
        filter_field, snr_field, count_field = _row_fields(row)
        if cleaned.get(filter_field):
            exposures.append(
                FilterExposure(
                    filter_id=cleaned[filter_field],
                    target_snr=cleaned[snr_field],
                    count=cleaned[count_field],
                )
            )
    return SkynetImagingRequest(
        name=cleaned["name"],
        grant_id=cleaned["grant"],
        target=SiderealTarget(
            name=target.name[:50], type="ICRS", ra=target.ra, dec=target.dec
        ),
        window=Window(start=cleaned["start"], end=cleaned["end"]),
        exposures=exposures,
        source=SourceModel(
            magnitude=cleaned["magnitude"],
            filter_id=cleaned["magnitude_filter"],
            measured_at=cleaned["measured_at"],
            event_time=cleaned["event_time"],
            decay_index=cleaned["decay_index"],
            spectral_index=cleaned["spectral_index"],
        ),
        max_airmass=cleaned.get("max_airmass"),
    )
