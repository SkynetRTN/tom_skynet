from django.apps import AppConfig


class TomSkynetConfig(AppConfig):
    """TOM Toolkit integration points for the Skynet facility"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "tom_skynet"
    verbose_name = "Skynet"

    def observation_facilities(self):
        return [{"class": f"{self.name}.facility.SkynetFacility"}]
