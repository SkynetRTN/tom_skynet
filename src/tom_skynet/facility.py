from tom_observations.facility import BaseRoboticObservationFacility

from .credentials import FACILITY_NAME
from .forms import SkynetImagingForm


class SkynetSubmitError(Exception):
    """Raised when a request submitted without the form isn't accepted"""


class SkynetFacility(BaseRoboticObservationFacility):
    """Skynet Robotic Telescope Network facility"""

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
        raise NotImplementedError

    def get_terminal_observing_states(self):
        return ["COMPLETED", "PARTIALLY_COMPLETED", "CANCELED", "WINDOW_EXPIRED"]

    def get_failed_observing_states(self):
        return ["WINDOW_EXPIRED"]

    def get_observing_sites(self):
        return {}

    def get_observation_status(self, observation_id):
        raise NotImplementedError

    def data_products(self, observation_id, product_id=None):
        raise NotImplementedError
