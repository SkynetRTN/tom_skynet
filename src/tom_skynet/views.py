from aeonlib.exceptions import AuthenticationError, ServiceNetworkError
from aeonlib.skynet import SkynetError
from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import FormView
from tom_common.encryption import EncryptedFormField

from . import client
from .credentials import settings_for_token
from .models import SkynetProfile


class SkynetTokenForm(forms.Form):
    api_token = EncryptedFormField(
        label="Skynet API token",
        help_text="An unscoped personal API token, from API tokens in your Skynet "
        "account settings. It is stored encrypted.",
    )


class SkynetTokenView(LoginRequiredMixin, FormView):
    """Set or clear the logged-in user's own Skynet API token"""

    form_class = SkynetTokenForm
    template_name = "tom_skynet/token.html"

    def get_success_url(self):
        return reverse("user-profile")

    def post(self, request, *args, **kwargs):
        if "clear" in request.POST:
            SkynetProfile.objects.filter(user=request.user).update(api_token=None)
            messages.success(request, "Your Skynet API token was removed.")
            return redirect(self.get_success_url())
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        token = form.cleaned_data["api_token"]
        # Check it before storing, so a typo shows up here, not at the first submit
        try:
            client.AeonlibSkynetFacility(
                settings=settings_for_token(token)
            ).instrument_pools()
        except AuthenticationError:
            form.add_error("api_token", "Skynet didn't accept this token.")
            return self.form_invalid(form)
        except (ServiceNetworkError, SkynetError) as exc:
            form.add_error(
                "api_token",
                f"Skynet couldn't check the token: {client.error_message(exc)}",
            )
            return self.form_invalid(form)
        profile, _ = SkynetProfile.objects.get_or_create(user=self.request.user)
        profile.api_token = token
        profile.save()
        messages.success(self.request, "Your Skynet API token was saved.")
        return super().form_valid(form)
