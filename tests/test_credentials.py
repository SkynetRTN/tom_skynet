import io

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.management import CommandError, call_command
from django.db import connection
from tom_observations.models import ObservationRecord
from tom_targets.models import Target

from tom_skynet.credentials import (
    SkynetCredentialsError,
    settings_for_observation,
    settings_for_user,
)
from tom_skynet.models import SkynetProfile

TOKEN = "qsty2RPtl6Zko9Ea-personal-token"
SKYNET = {
    "api_root": "http://public-api:5001/",
    "web_root": "http://127.0.0.1:4200/",
}

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="ada", password="x" * 16)


@pytest.fixture
def skynet_settings(settings):
    settings.FACILITIES = {"SKYNET": dict(SKYNET)}
    return settings


def record(owner, observation_id="310"):
    target = Target.objects.create(
        name=f"AT {observation_id}", type=Target.SIDEREAL, ra=150.0, dec=-30.0
    )
    return ObservationRecord.objects.create(
        target=target,
        user=owner,
        facility="Skynet",
        observation_id=observation_id,
        parameters={},
    )


class TestProfile:
    def test_the_token_is_encrypted_at_rest(self, user):
        SkynetProfile.objects.create(user=user, api_token=TOKEN)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT api_token FROM {SkynetProfile._meta.db_table} WHERE user_id = %s",
                [user.pk],
            )
            (stored,) = cursor.fetchone()
        stored = bytes(stored)
        assert stored and TOKEN.encode() not in stored

    def test_the_token_reads_back_decrypted(self, user):
        SkynetProfile.objects.create(user=user, api_token=TOKEN)
        assert SkynetProfile.objects.get(user=user).api_token == TOKEN


class TestSettingsForUser:
    def test_the_users_token_and_the_facility_roots(self, user, skynet_settings):
        SkynetProfile.objects.create(user=user, api_token=TOKEN)
        aeon = settings_for_user(user)
        assert aeon.skynet_token == TOKEN
        assert aeon.skynet_api_root == SKYNET["api_root"]
        assert aeon.skynet_web_root == SKYNET["web_root"]

    def test_unset_roots_fall_back_to_aeonlibs_defaults(self, user, settings):
        settings.FACILITIES = {}
        SkynetProfile.objects.create(user=user, api_token=TOKEN)
        aeon = settings_for_user(user)
        assert aeon.skynet_api_root == "https://api.skynetgo.org/"
        assert aeon.skynet_web_root == "https://skynetgo.org/"

    @pytest.mark.parametrize("stored", [None, ""])
    def test_no_token_names_the_user(self, user, skynet_settings, stored):
        SkynetProfile.objects.create(user=user, api_token=stored)
        with pytest.raises(SkynetCredentialsError, match="No Skynet API token for ada"):
            settings_for_user(user)

    def test_no_profile_names_the_user(self, user, skynet_settings):
        with pytest.raises(SkynetCredentialsError, match="for ada"):
            settings_for_user(user)

    @pytest.mark.parametrize("anonymous", [None, AnonymousUser()])
    def test_an_anonymous_user_has_no_token(self, anonymous, skynet_settings):
        with pytest.raises(SkynetCredentialsError):
            settings_for_user(anonymous)

    def test_a_shared_facility_token_is_only_a_fallback(self, user, settings):
        settings.FACILITIES = {"SKYNET": SKYNET | {"api_token": "shared-token"}}
        assert settings_for_user(user).skynet_token == "shared-token"
        SkynetProfile.objects.create(user=user, api_token=TOKEN)
        assert settings_for_user(user).skynet_token == TOKEN


class TestSettingsForObservation:
    def test_the_record_owners_token(self, user, django_user_model, skynet_settings):
        other = django_user_model.objects.create_user(username="bob", password="y" * 16)
        SkynetProfile.objects.create(user=user, api_token=TOKEN)
        SkynetProfile.objects.create(user=other, api_token="bobs-token")
        record(user, "310")
        record(other, "311")
        assert settings_for_observation(310).skynet_token == TOKEN
        assert settings_for_observation("311").skynet_token == "bobs-token"

    def test_an_owner_without_a_token_is_named(self, user, skynet_settings):
        record(user)
        with pytest.raises(SkynetCredentialsError, match="for ada"):
            settings_for_observation("310")

    def test_a_record_without_an_owner(self, skynet_settings):
        record(None)
        with pytest.raises(SkynetCredentialsError, match="has no owner"):
            settings_for_observation("310")

    def test_an_unknown_observation(self, skynet_settings):
        with pytest.raises(
            SkynetCredentialsError, match="No Skynet observation record 999"
        ):
            settings_for_observation("999")

    def test_another_facilitys_record_is_not_used(self, user, skynet_settings):
        SkynetProfile.objects.create(user=user, api_token=TOKEN)
        record(user).__class__.objects.update(facility="LCO")
        with pytest.raises(
            SkynetCredentialsError, match="No Skynet observation record"
        ):
            settings_for_observation("310")


class TestSetTokenCommand:
    def test_reads_the_token_from_stdin(self, user, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO(f"{TOKEN}\n"))
        out = io.StringIO()
        call_command("skynet_set_token", "ada", "--stdin", stdout=out)
        assert SkynetProfile.objects.get(user=user).api_token == TOKEN
        assert "Stored a Skynet API token for ada" in out.getvalue()
        assert TOKEN not in out.getvalue()

    def test_prompts_without_echo(self, user, monkeypatch):
        monkeypatch.setattr(
            "tom_skynet.management.commands.skynet_set_token.getpass",
            lambda prompt: TOKEN,
        )
        call_command("skynet_set_token", "ada", stdout=io.StringIO())
        assert SkynetProfile.objects.get(user=user).api_token == TOKEN

    def test_replaces_and_clears(self, user, monkeypatch):
        SkynetProfile.objects.create(user=user, api_token="old")
        monkeypatch.setattr("sys.stdin", io.StringIO("new\n"))
        call_command("skynet_set_token", "ada", "--stdin", stdout=io.StringIO())
        assert SkynetProfile.objects.get(user=user).api_token == "new"
        call_command("skynet_set_token", "ada", "--clear", stdout=io.StringIO())
        assert SkynetProfile.objects.get(user=user).api_token is None

    def test_refuses_an_empty_token(self, user, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
        with pytest.raises(CommandError, match="empty"):
            call_command("skynet_set_token", "ada", "--stdin")

    def test_refuses_an_unknown_user(self):
        with pytest.raises(CommandError, match="No TOM user 'nobody'"):
            call_command("skynet_set_token", "nobody", "--clear")
