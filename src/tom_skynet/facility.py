import json
import logging

import requests
from aeonlib.exceptions import AuthenticationError, ServiceNetworkError
from aeonlib.skynet import STATES, ObservationProgress, SkynetError
from django import forms
from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models import Q
from tom_dataproducts import models as dataproduct_models
from tom_dataproducts.models import DataProduct
from tom_observations.facility import BaseRoboticObservationFacility
from tom_observations.models import ObservationRecord

from .client import error_message, facility_for_observation
from .credentials import FACILITY_NAME, SkynetCredentialsError, web_root
from .forms import SkynetImagingForm
from .models import SkynetRecordState

logger = logging.getLogger(__name__)

# Keeps Skynet's file ids apart from other facilities' in the TOM-wide
# DataProduct.product_id
PRODUCT_PREFIX = "skynet:"

DOWNLOAD_TIMEOUT_SEC = 120

# TOM Toolkit's data product type for FITS images, when the TOM defines it
FITS_FILE = "fits_file"


class SkynetSubmitError(Exception):
    """Raised when a request submitted without the form isn't accepted"""


class SkynetFacility(BaseRoboticObservationFacility):
    """
    Skynet Robotic Telescope Network facility

    Methods that take an observation id act as the TOM user who owns the record,
    whoever set_user names: that user's token can read the observation.
    """

    name = FACILITY_NAME

    def __init__(self):
        super().__init__()
        self.observation_forms = {"IMAGING": SkynetImagingForm}

    def get_form(self, observation_type):
        return SkynetImagingForm

    def _form(self, observation_payload: dict, submit: bool) -> SkynetImagingForm:
        data = {
            **observation_payload["params"],
            "target_id": observation_payload["target_id"],
        }
        if not submit:
            data["validate"] = "validate"
        return SkynetImagingForm(data=data, facility=self)

    def submit_observation(self, observation_payload):
        """
        Return the id of the observation the form submitted, or submit the
        payload's parameters as self.user when the form wasn't used
        """
        observation_id = observation_payload.get("skynet_observation_id")
        if observation_id is None:
            form = self._form(observation_payload, submit=True)
            if not form.is_valid() or form.submitted is None:
                raise SkynetSubmitError(" ".join(map(str, form.non_field_errors())))
            observation_id = form.submitted.id
        return [str(observation_id)]

    def validate_observation(self, observation_payload):
        """
        Check the request with Skynet as self.user, without submitting: the
        form's checks, then Skynet's. Messages for the fields, then the rest.
        """
        form = self._form(observation_payload, submit=False)
        form.is_valid()
        return [str(error) for error in form.non_field_errors()]

    def get_observation_url(self, observation_id):
        # AEONlib's observation_url, which needs a token to build the facility
        return f"{web_root().rstrip('/')}/observations/{observation_id}"

    def get_terminal_observing_states(self):
        return ["COMPLETED", "PARTIALLY_COMPLETED", "CANCELED", "WINDOW_EXPIRED"]

    def get_failed_observing_states(self):
        return ["WINDOW_EXPIRED"]

    def get_observing_sites(self):
        """FACILITIES["SKYNET"]["sites"], in TOM Toolkit's format; none by default"""
        facility = getattr(settings, "FACILITIES", {}).get("SKYNET", {})
        return facility.get("sites", {})

    def cancel_observation(self, observation_id):
        """
        Cancel the observation on Skynet as the record's owner. Frames already
        taken are kept, so it can end PARTIALLY_COMPLETED rather than CANCELED.
        """
        record = (
            ObservationRecord.objects.filter(
                facility=self.name, observation_id=str(observation_id)
            )
            .select_related("user")
            .first()
        )
        # TOM Toolkit's cancel view only requires a login, and the cancel goes
        # out with the owner's token
        user = self.user
        if (
            record is not None
            and user is not None
            and user != record.user
            and not user.is_superuser
        ):
            raise forms.ValidationError(
                "Only the TOM user who submitted it can cancel a Skynet observation"
            )
        try:
            skynet = facility_for_observation(observation_id)
            if skynet.state(observation_id).terminal:
                return True
            skynet.cancel(observation_id)
        except (
            SkynetCredentialsError,
            AuthenticationError,
            ServiceNetworkError,
            SkynetError,
        ) as exc:
            raise forms.ValidationError(
                f"Skynet didn't cancel it: {error_message(exc)}"
            ) from exc
        return True

    def get_observation_status(self, observation_id):
        state = facility_for_observation(observation_id).state(observation_id)
        return {
            "state": state.state,
            "scheduled_start": state.scheduled_start,
            "scheduled_end": state.scheduled_end,
        }

    def update_observation_status(self, observation_id):
        """Write the state, then save any final products not saved yet"""
        progress = facility_for_observation(observation_id).progress(observation_id)
        records = ObservationRecord.objects.filter(
            facility=self.name, observation_id=str(observation_id)
        )
        for record in records:
            record.status = STATES[progress.state]
            record.scheduled_start = progress.scheduled_start
            record.scheduled_end = progress.scheduled_end
            record.save()
            if _unsaved_final_products(progress):
                self.save_data_products(record, progress=progress)
            # Finished only once every final product the progress lists is
            # saved: Skynet may not presign some (they come without a URL)
            missing = _unsaved_final_products(progress)
            if missing:
                logger.warning(
                    "Skynet observation %s: %d final product(s) not saved yet "
                    "(no download URL); will retry: %s",
                    observation_id,
                    len(missing),
                    ", ".join(sorted(missing)),
                )
            SkynetRecordState.objects.update_or_create(
                record=record,
                defaults={"products_final": progress.products_final and not missing},
            )

    def update_all_observation_statuses(self, target=None):
        """
        Poll the records that aren't terminal, and the terminal ones whose
        products aren't final yet: processing ends after the observation does
        """
        records = ObservationRecord.objects.filter(facility=self.name)
        if target:
            records = records.filter(target=target)
        records = records.exclude(
            Q(status__in=self.get_terminal_observing_states())
            & Q(skynet_state__products_final=True)
        )
        failed_records = []
        for observation_id in records.values_list(
            "observation_id", flat=True
        ).distinct():
            try:
                self.update_observation_status(observation_id)
            # Like the base class: one record's failure mustn't stop the rest
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skynet observation %s: %s", observation_id, exc)
                failed_records.append((observation_id, str(exc)))
        return failed_records

    def all_data_products(self, observation_record):
        """The record page's products; only the saved ones when Skynet can't be read"""
        try:
            return super().all_data_products(observation_record)
        except (
            SkynetCredentialsError,
            AuthenticationError,
            ServiceNetworkError,
            SkynetError,
        ) as exc:
            logger.warning(
                "Skynet observation %s: %s", observation_record.observation_id, exc
            )
            saved = DataProduct.objects.filter(observation_record=observation_record)
            return {"saved": list(saved), "unsaved": []}

    def save_data_products(self, observation_record, product_id=None, progress=None):
        """
        Download and save the final products not saved yet. Unlike the base
        class, the DataProduct is only written once its file has downloaded, so
        a failed download (an expired URL, Skynet's storage unreachable) leaves
        nothing behind and is retried on the next poll. Pass a progress already
        read to save reading it again.
        """
        from tom_dataproducts.utils import create_image_dataproduct

        saved = []
        telescopes = None
        for product in self.data_products(
            observation_record.observation_id, product_id, progress=progress
        ):
            existing = DataProduct.objects.filter(product_id=product["id"]).first()
            if existing is not None and existing.data:
                saved.append(existing)
                continue
            if product["url"] is None:
                # Skynet didn't presign it this time; the next poll asks again
                continue
            response = requests.get(product["url"], timeout=DOWNLOAD_TIMEOUT_SEC)
            response.raise_for_status()
            data_product = existing or DataProduct(
                product_id=product["id"],
                target=observation_record.target,
                observation_record=observation_record,
            )
            if telescopes is None:
                telescopes = _telescopes(observation_record.observation_id)
            data_product.data_product_type = _fits_type()
            data_product.extra_data = json.dumps(
                _metadata(product["skynet"], telescopes)
            )
            data_product.data.save(product["filename"], ContentFile(response.content))
            logger.info("Saved Skynet data product %s", data_product.data)
            if getattr(settings, "AUTO_THUMBNAILS", False):
                create_image_dataproduct(data_product)
                data_product.get_preview()
            saved.append(data_product)
        return saved

    def data_products(self, observation_id, product_id=None, progress=None):
        """
        The final science files, with presigned URLs; a re-processed frame is a
        new file. "data" is the URL too, which the record page links.
        """
        skynet = facility_for_observation(observation_id)
        products = [
            {
                "id": PRODUCT_PREFIX + product.file_id,
                "filename": product.filename,
                "url": product.url,
                "data": product.url,
                "created": product.taken_at_start_utc,
                "skynet": product,
            }
            for product in skynet.data_products(observation_id, progress=progress)
        ]
        if product_id is not None:
            products = [p for p in products if p["id"] == product_id]
        return products


def _unsaved_final_products(progress: ObservationProgress) -> set[str]:
    final = {PRODUCT_PREFIX + p.file_id for p in progress.products if p.is_final}
    saved = (
        DataProduct.objects.filter(product_id__in=final)
        .exclude(data="")
        .values_list("product_id", flat=True)
    )
    return final - set(saved)


def _fits_type() -> str:
    """fits_file when the TOM's DATA_PRODUCT_TYPES has it, else no type"""
    types = {name for name, _ in dataproduct_models.DATA_TYPE_CHOICES}
    if FITS_FILE in types:
        return FITS_FILE
    logger.warning(
        "DATA_PRODUCT_TYPES has no %r; Skynet products are saved without a type",
        FITS_FILE,
    )
    return ""


def _telescopes(observation_id) -> dict:
    """The record owner's telescopes by id, or nothing when the pools can't be read"""
    try:
        pools = facility_for_observation(observation_id).instrument_pools()
    except (
        SkynetCredentialsError,
        AuthenticationError,
        ServiceNetworkError,
        SkynetError,
    ):
        return {}
    return pools.telescopes


def _metadata(product, telescopes: dict) -> dict:
    """The D9 metadata saved in DataProduct.extra_data, units in the names"""
    telescope = telescopes.get(product.telescope_id)
    metadata = {
        "skynet_file_id": product.file_id,
        "is_reduced": product.is_reduced,
        "telescope_id": product.telescope_id,
        "telescope_name": telescope.name if telescope else None,
        "telescope_slug": telescope.slug if telescope else None,
        "instrument_id": product.instrument_id,
        "filter_id": product.filter_id,
        "exposure_time_sec": product.exposure_time_sec,
        "taken_at_start_utc": product.taken_at_start_utc.isoformat()
        if product.taken_at_start_utc
        else None,
        "airmass": product.airmass,
        "pixel_scale_arcsec_per_px": product.pixel_scale_arcsec_per_px,
    }
    return {key: value for key, value in metadata.items() if value is not None}
