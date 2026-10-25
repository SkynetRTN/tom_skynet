import pytest
from crispy_forms.utils import render_crispy_form
from django.urls import reverse

from tom_skynet.facility import SkynetFacility
from tom_skynet.forms import SkynetImagingForm
from tom_skynet.models import SkynetProfile

pytestmark = pytest.mark.django_db

TOKEN_URL = "/skynet/token/"


@pytest.fixture
def ada(django_user_model):
    return django_user_model.objects.create_user(username="ada", password="x" * 16)


def profile_page(client, user) -> str:
    client.force_login(user)
    response = client.get(reverse("user-profile"))
    assert response.status_code == 200
    return response.content.decode()


class TestProfileCard:
    def test_without_a_token(self, client, ada, skynet_api):
        page = profile_page(client, ada)
        assert "No API token" in page
        assert f'href="{TOKEN_URL}"' in page
        assert not skynet_api.requests

    def test_with_a_token_it_lists_the_usable_accounts(self, client, ada, skynet_api):
        SkynetProfile.objects.create(user=ada, api_token="ada-token")
        page = profile_page(client, ada)
        assert "API token set" in page
        assert "Ada&#x27;s account" in page and "Transient team" in page

    def test_the_sim_guard_applies(self, client, ada, skynet_api, settings):
        settings.FACILITIES["SKYNET"]["allowed_telescope_prefix"] = "sim-"
        SkynetProfile.objects.create(user=ada, api_token="ada-token")
        page = profile_page(client, ada)
        assert "Ada&#x27;s account" in page and "Transient team" not in page

    def test_a_token_skynet_refuses(self, client, ada, skynet_api):
        skynet_api.rejected_tokens = {"old-token"}
        SkynetProfile.objects.create(user=ada, api_token="old-token")
        assert "Skynet didn&#x27;t accept this token." in profile_page(client, ada)


class TestTokenPage:
    def test_needs_a_login(self, client):
        response = client.get(TOKEN_URL)
        assert response.status_code == 302
        assert "login" in response["Location"]

    def test_the_token_is_checked_then_stored_encrypted(self, client, ada, skynet_api):
        client.force_login(ada)
        response = client.post(TOKEN_URL, {"api_token": "new-token", "save": "Save"})
        assert response.status_code == 302
        assert response["Location"] == reverse("user-profile")
        (check,) = skynet_api.requests
        assert check.headers["Authorization"] == "Bearer new-token"
        assert SkynetProfile.objects.get(user=ada).api_token == "new-token"

    def test_a_token_skynet_refuses_is_not_stored(self, client, ada, skynet_api):
        skynet_api.rejected_tokens = {"typo"}
        client.force_login(ada)
        response = client.post(TOKEN_URL, {"api_token": "typo", "save": "Save"})
        assert response.status_code == 200
        assert "Skynet didn&#x27;t accept this token." in response.content.decode()
        assert not SkynetProfile.objects.filter(user=ada).exists()

    def test_the_page_never_shows_the_token(self, client, ada, skynet_api):
        SkynetProfile.objects.create(user=ada, api_token="secret-token")
        client.force_login(ada)
        assert "secret-token" not in client.get(TOKEN_URL).content.decode()

    def test_remove(self, client, ada, skynet_api):
        SkynetProfile.objects.create(user=ada, api_token="ada-token")
        client.force_login(ada)
        response = client.post(TOKEN_URL, {"clear": "Remove token"})
        assert response.status_code == 302
        assert SkynetProfile.objects.get(user=ada).api_token is None


def test_the_request_form_links_to_the_token_page(ada, skynet_api):
    facility = SkynetFacility()
    facility.set_user(ada)
    form = SkynetImagingForm(initial={"target_id": 1}, facility=facility)
    assert form.needs_token
    assert f'href="{TOKEN_URL}"' in render_crispy_form(form)
