import logging

from aeonlib.exceptions import AuthenticationError, ServiceNetworkError
from aeonlib.skynet import STATES, ObservationProgress, SkynetError
from django.db.models import Q
from tom_dataproducts.models import DataProduct
from tom_observations.facility import BaseRoboticObservationFacility
from tom_observations.models import ObservationRecord

from .client import facility_for_observation
from .credentials import FACILITY_NAME, SkynetCredentialsError, web_root
from .forms import SkynetImagingForm
from .models import SkynetRecordState

logger = logging.getLogger(__name__)

# Keeps Skynet's file ids apart from other facilities' in the TOM-wide
# DataProduct.product_id
PRODUCT_PREFIX = "skynet:"


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
        Check the request against the user's time accounts, without submitting.
        Skynet has no dry run yet.
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
        return {}

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
                self.save_data_products(record)
            # Only once the products are saved, so a failed download is retried
            SkynetRecordState.objects.update_or_create(
                record=record, defaults={"products_final": progress.products_final}
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

    def data_products(self, observation_id, product_id=None):
        """The final science files, with presigned URLs; a re-processed frame is a new file"""
        skynet = facility_for_observation(observation_id)
        products = [
            {
                "id": PRODUCT_PREFIX + product.file_id,
                "filename": product.filename,
                "url": product.url,
                "created": product.taken_at_start_utc,
            }
            for product in skynet.data_products(observation_id)
        ]
        if product_id is not None:
            products = [p for p in products if p["id"] == product_id]
        return products


def _unsaved_final_products(progress: ObservationProgress) -> set[str]:
    final = {PRODUCT_PREFIX + p.file_id for p in progress.products if p.is_final}
    saved = DataProduct.objects.filter(product_id__in=final).values_list(
        "product_id", flat=True
    )
    return final - set(saved)
