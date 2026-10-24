from tom_observations.facility import BaseRoboticObservationFacility


class SkynetFacility(BaseRoboticObservationFacility):
    """Skynet Robotic Telescope Network facility"""

    name = "Skynet"

    def get_form(self, observation_type):
        raise NotImplementedError

    def submit_observation(self, observation_payload):
        raise NotImplementedError

    def validate_observation(self, observation_payload):
        raise NotImplementedError

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
