from datetime import UTC, datetime, timedelta
from typing import cast

from aeonlib.exceptions import AuthenticationError, ServiceNetworkError
from aeonlib.models import SiderealTarget, Window
from aeonlib.skynet import (
    FilterExposure,
    InstrumentPool,
    InstrumentPools,
    InstrumentPreview,
    SkynetError,
    SkynetImagingRequest,
    SourceModel,
    SubmittedObservation,
)
from crispy_forms.layout import HTML, Column, Fieldset, Layout, Row
from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from pydantic import ValidationError
from tom_observations.facility import BaseRoboticObservationForm
from tom_targets.models import Target

from .client import error_message, facility_for_user
from .credentials import SkynetCredentialsError
from .pools import pool_labels, visible_pools
from .preview import preview_refusal, preview_summary

FILTER_ROWS = 3
SNR, EXPOSURE_TIME = "snr", "exposure_time"
SOURCE_FIELDS = (
    "magnitude",
    "magnitude_filter",
    "measured_at",
    "event_time",
    "decay_index",
    "spectral_index",
)
SKYNET_ERRORS = (SkynetError, AuthenticationError, ServiceNetworkError)
NO_USER = (
    "Skynet requests need a TOM user: submit them from a target's Observe tab. "
    "TOM Toolkit's REST API and cadences aren't supported."
)


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


def _row_fields(row: int) -> tuple[str, str, str, str]:
    return f"filter_{row}", f"target_snr_{row}", f"exposure_time_{row}", f"count_{row}"


# AEONlib request fields, as SkynetFacility.validate names them, to form fields
REQUEST_FIELDS = {
    "name": "name",
    "grant_id": "grant",
    "window.start": "start",
    "window.end": "end",
    "source.magnitude": "magnitude",
    "source.filter_id": "magnitude_filter",
    "source.measured_at": "measured_at",
    "source.event_time": "event_time",
    "source.decay_index": "decay_index",
    "source.spectral_index": "spectral_index",
    "source.extinction_av": "extinction_av",
    "source.rv": "rv",
    "max_airmass": "max_airmass",
    "min_moon_separation": "min_moon_separation",
    "epoch_count": "epoch_count",
    "cadence_period": "cadence_period_hours",
}
EXPOSURE_FIELDS = {
    "filter_id": "filter",
    "target_snr": "target_snr",
    "total_exposure_time": "exposure_time",
    "count": "count",
}


class SkynetImagingForm(BaseRoboticObservationForm):
    """
    An optical imaging request, sized by target SNR or by total exposure time;
    Skynet chooses the telescope.

    Validate checks the request with Skynet and previews which telescopes can
    carry it out. Submit refuses a request no telescope can carry out in its
    window, and otherwise submits it from the form: TOM Toolkit's create view
    doesn't catch errors from submit_observation, so submitting here is what
    lets a refusal reach the page as a form error.
    """

    name = forms.CharField(max_length=256)
    grant = forms.TypedChoiceField(coerce=int, label="Time account")
    start = UTCDateTimeField(help_text="UTC")
    end = UTCDateTimeField(help_text="UTC")
    exposure_mode = forms.ChoiceField(
        choices=[(SNR, "Target SNR"), (EXPOSURE_TIME, "Total exposure time")],
        initial=SNR,
        required=False,
        label="Size exposures by",
        help_text="With a target SNR, Skynet sizes the exposures for each "
        "telescope from the source brightness",
    )

    magnitude = forms.FloatField(required=False, label="Reference magnitude (AB)")
    magnitude_filter = forms.ChoiceField(required=False, label="Reference filter")
    measured_at = UTCDateTimeField(required=False, label="Measured at", help_text="UTC")
    event_time = UTCDateTimeField(
        required=False, label="Event time (T0)", help_text="UTC"
    )
    decay_index = forms.FloatField(required=False, label="Temporal index (alpha)")
    spectral_index = forms.FloatField(required=False, label="Spectral index (beta)")
    extinction_av = forms.FloatField(
        required=False, min_value=0.0, initial=0.0, label="Extinction A_V (mag)"
    )
    rv = forms.FloatField(required=False, min_value=0.1, initial=3.1, label="R_V")

    max_airmass = forms.FloatField(required=False, min_value=1.0, initial=2.0)
    min_moon_separation = forms.FloatField(
        required=False,
        min_value=0.0,
        max_value=180.0,
        label="Minimum Moon separation (degrees)",
    )
    epoch_count = forms.IntegerField(
        required=False,
        min_value=1,
        initial=1,
        label="Epochs",
        help_text="Repeat every exposure this many times",
    )
    cadence_period_hours = forms.FloatField(
        required=False,
        min_value=0.0,
        label="Repeat every (hours)",
        help_text="From the start of one epoch to the start of the next",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for row in range(1, FILTER_ROWS + 1):
            filter_field, snr_field, time_field, count_field = _row_fields(row)
            self.fields[filter_field] = forms.ChoiceField(
                required=row == 1, label="Filter"
            )
            self.fields[snr_field] = forms.FloatField(
                required=False, min_value=0.0, label="Target SNR"
            )
            self.fields[time_field] = forms.FloatField(
                required=False, min_value=0.0, label="Total exposure time (s)"
            )
            self.fields[count_field] = forms.IntegerField(
                required=False, min_value=1, initial=1, label="Count"
            )

        self.skynet = None
        self.pools: InstrumentPools | None = None
        self.unavailable = ""
        self.needs_token = False
        self.skynet_request: SkynetImagingRequest | None = None
        self.submitted: SubmittedObservation | None = None
        self.previews: list[InstrumentPreview] | None = None
        #: The form row of each of the request's exposures
        self.exposure_rows: list[int] = []
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
        if user is None:
            # TOM Toolkit's REST API and cadence strategies build the form
            # without one, and the token belongs to a user
            self.unavailable = NO_USER
            return
        try:
            self.skynet = facility_for_user(user)
            self.pools = self.skynet.instrument_pools()
        except SkynetCredentialsError as exc:
            self.unavailable = f"{exc}. Set your Skynet API token first."
            self.needs_token = True
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
        link = (
            f' <a href="{reverse("tom_skynet:token")}">Set your Skynet API token</a>'
            if self.needs_token
            else ""
        )
        alert = (
            HTML(
                f'<div class="alert alert-warning">{escape(self.unavailable)}{link}</div>'
            )
            if self.unavailable
            else HTML("")
        )
        return Layout(
            alert,
            Fieldset("Request", "name", "grant", Row(Column("start"), Column("end"))),
            Fieldset("Exposures", "exposure_mode", *rows),
            Fieldset(
                "Source brightness (for a target SNR)",
                Row(Column("magnitude"), Column("magnitude_filter")),
                Row(Column("measured_at"), Column("event_time")),
                Row(Column("decay_index"), Column("spectral_index")),
                Row(Column("extinction_av"), Column("rv")),
            ),
            Fieldset(
                "Cadence",
                Row(Column("epoch_count"), Column("cadence_period_hours")),
            ),
            Fieldset(
                "Constraints",
                Row(Column("max_airmass"), Column("min_moon_separation")),
            ),
        )

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        if self.unavailable:
            raise forms.ValidationError(self.unavailable)
        if self.errors:
            return cleaned

        # Parameters saved before these fields existed have neither
        cleaned["exposure_mode"] = cleaned.get("exposure_mode") or SNR
        cleaned["epoch_count"] = cleaned.get("epoch_count") or 1
        by_snr = cleaned["exposure_mode"] == SNR
        exposures = []
        for row in range(1, FILTER_ROWS + 1):
            filter_field, snr_field, time_field, count_field = _row_fields(row)
            filter_id = cleaned.get(filter_field)
            if not filter_id:
                continue
            for field in (snr_field if by_snr else time_field, count_field):
                if cleaned.get(field) is None:
                    self.add_error(field, "Required when a filter is chosen.")
            exposures.append((row, filter_id))
        if by_snr:
            for field in SOURCE_FIELDS:
                if cleaned.get(field) in (None, ""):
                    self.add_error(field, "Required for a target SNR.")
        if cleaned["epoch_count"] > 1 and not cleaned.get("cadence_period_hours"):
            self.add_error("cadence_period_hours", "Required for more than one epoch.")
        if self.errors:
            return cleaned

        assert self.pools is not None
        pool = self.pools.pool(cleaned["grant"])
        self._check_limits(pool, exposures, by_snr)
        if self.errors:
            return cleaned
        self.exposure_rows = [row for row, _ in exposures]

        target = Target.objects.get(pk=cleaned["target_id"])
        try:
            self.skynet_request = build_request(cleaned, target)
        except ValidationError as exc:
            for error in exc.errors():
                self.add_error(None, error["msg"].removeprefix("Value error, "))
        except ValueError as exc:
            self.add_error(None, str(exc))
        return cleaned

    def _check_limits(
        self, pool: InstrumentPool, exposures: list[tuple[int, str]], by_snr: bool
    ) -> None:
        """Check the rows against what the time account's telescopes offer"""
        cleaned = self.cleaned_data
        limits = pool.limits
        for row, filter_id in exposures:
            if filter_id not in pool.filter_instrument_ids:
                self.add_error(
                    f"filter_{row}",
                    f"{filter_id} isn't offered by this time account's telescopes.",
                )
            if by_snr:
                field, low, high = (
                    f"target_snr_{row}",
                    limits.min_target_snr,
                    limits.max_target_snr,
                )
                message = "Choose a target SNR from {low} to {high}."
            else:
                field, low, high = (
                    f"exposure_time_{row}",
                    limits.min_exposure_time_sec,
                    limits.max_exposure_time_sec,
                )
                message = "Choose a total exposure time from {low} to {high} s."
            value = cleaned[field]
            if (low is not None and value < low) or (high is not None and value > high):
                self.add_error(field, message.format(low=low, high=high))
        epochs, most = cleaned["epoch_count"], limits.max_epoch_count
        if most is not None and epochs > most:
            self.add_error("epoch_count", f"Choose at most {most} epochs.")

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
            for message in errors:
                self.add_error(None, f"{self._label(name)}: {message}")

    def _label(self, name: str) -> str:
        return str(self.fields[name].label or name.replace("_", " ").capitalize())

    def is_valid(self):
        valid = super().is_valid()
        if valid and self.submitted is None:
            if "validate" in self.data:
                self._validate()
            else:
                self._submit()
        return not self.errors

    def _validate(self) -> None:
        """Check the request with Skynet, then preview the telescopes"""
        assert self.skynet is not None and self.skynet_request is not None
        try:
            result = self.skynet.validate(self.skynet_request)
        except SKYNET_ERRORS as exc:
            self.add_error(
                None, f"Skynet couldn't check the request: {error_message(exc)}"
            )
            return
        if not result.valid:
            for path, messages in result.errors.items():
                field = self.form_field(path)
                for message in messages:
                    self.add_error(field, message)
                    if field is not None:
                        # Repeated where the TOM shows it, as full_clean does
                        self.add_error(None, f"{self._label(field)}: {message}")
            return
        if self._preview():
            assert self.previews is not None
            self.validation_message = preview_summary(
                self.previews, self.skynet_request
            )

    def _preview(self) -> bool:
        assert self.skynet is not None and self.skynet_request is not None
        try:
            self.previews = self.skynet.instrument_preview(self.skynet_request)
        except (ValueError, *SKYNET_ERRORS) as exc:
            self.add_error(
                None, f"Skynet couldn't check the telescopes: {error_message(exc)}"
            )
            return False
        return True

    def _submit(self) -> None:
        assert self.skynet is not None and self.skynet_request is not None
        if not self._preview():
            return
        assert self.previews is not None
        refusal = preview_refusal(self.previews, self.skynet_request)
        if refusal:
            self.add_error(None, refusal)
            return
        try:
            self.submitted = self.skynet.submit(self.skynet_request)
        except (ValueError, *SKYNET_ERRORS) as exc:
            self.add_error(
                None, f"Skynet didn't accept the request: {error_message(exc)}"
            )

    def form_field(self, path: str) -> str | None:
        """The form field for an AEONlib request field path, or None"""
        if path in REQUEST_FIELDS:
            return REQUEST_FIELDS[path]
        parts = path.split(".")
        if len(parts) == 3 and parts[0] == "exposures" and parts[1].isdigit():
            index = int(parts[1])
            if index < len(self.exposure_rows) and parts[2] in EXPOSURE_FIELDS:
                return f"{EXPOSURE_FIELDS[parts[2]]}_{self.exposure_rows[index]}"
        return None

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
    by_snr = cleaned.get("exposure_mode", SNR) == SNR
    exposures = []
    for row in range(1, FILTER_ROWS + 1):
        filter_field, snr_field, time_field, count_field = _row_fields(row)
        if cleaned.get(filter_field):
            exposures.append(
                FilterExposure(
                    filter_id=cleaned[filter_field],
                    target_snr=cleaned[snr_field] if by_snr else None,
                    total_exposure_time=None if by_snr else cleaned[time_field],
                    count=cleaned[count_field],
                )
            )
    source = None
    if by_snr:
        source = SourceModel(
            magnitude=cleaned["magnitude"],
            filter_id=cleaned["magnitude_filter"],
            measured_at=cleaned["measured_at"],
            event_time=cleaned["event_time"],
            decay_index=cleaned["decay_index"],
            spectral_index=cleaned["spectral_index"],
            extinction_av=_or(cleaned.get("extinction_av"), 0.0),
            rv=_or(cleaned.get("rv"), 3.1),
        )
    epochs = cleaned.get("epoch_count") or 1
    period_hours = cleaned.get("cadence_period_hours")
    return SkynetImagingRequest(
        name=cleaned["name"],
        grant_id=cleaned["grant"],
        target=SiderealTarget(
            name=target.name[:50], type="ICRS", ra=target.ra, dec=target.dec
        ),
        window=Window(start=cleaned["start"], end=cleaned["end"]),
        exposures=exposures,
        source=source,
        max_airmass=cleaned.get("max_airmass"),
        min_moon_separation=cleaned.get("min_moon_separation"),
        epoch_count=epochs,
        cadence_period=period_hours * 3600 if epochs > 1 and period_hours else None,
    )


def _or(value, default: float) -> float:
    return default if value is None else value
