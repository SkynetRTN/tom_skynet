from django.apps import AppConfig
from django.urls import include, path


class TomSkynetConfig(AppConfig):
    """TOM Toolkit integration points for the Skynet facility"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "tom_skynet"
    verbose_name = "Skynet"

    def observation_facilities(self):
        return [{"class": f"{self.name}.facility.SkynetFacility"}]

    def profile_details(self):
        return [
            {
                "partial": "tom_skynet/partials/skynet_profile.html",
                "context": f"{self.name}.profile.profile_card",
            }
        ]

    def include_url_paths(self):
        return [path("skynet/", include(f"{self.name}.urls"))]
