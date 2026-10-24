from django.conf import settings
from django.db import models
from tom_common.encryption import EncryptedModelField


class SkynetProfile(models.Model):
    """A TOM user's Skynet credentials"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="skynet_profile",
    )
    api_token = EncryptedModelField(null=True, blank=True)
    """An unscoped Skynet personal API token, encrypted at rest"""
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Skynet profile of {self.user}"
