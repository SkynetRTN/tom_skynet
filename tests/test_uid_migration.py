"""Migration 0004: the integer Skynet ids earlier versions stored become uids"""

import httpx
import pytest
from django.apps import apps
from tom_observations.models import ObservationRecord
from tom_targets.models import Target

from tom_skynet.models import SkynetProfile
from tom_skynet.uid_migration import UidMigrationError, migrate_records

pytestmark = pytest.mark.django_db

OBSERVATION_UID = "2c1aa637-0913-4a82-8701-b81a30e58dff"
GRANT_UID = "dd9463a0-b43e-4d0f-8def-90c688558d3c"


class OldSkynet:
    """Skynet during its dual period: integer paths still resolve, answers carry uids"""

    def __init__(self):
        self.observations = {"317": OBSERVATION_UID}
        self.grants = {"99": GRANT_UID}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.startswith("/v1/observations/"):
            table, key = self.observations, path.removeprefix("/v1/observations/")
        elif path.startswith("/v1/observing-grants/"):
            table, key = self.grants, path.removeprefix("/v1/observing-grants/")
        else:
            return httpx.Response(404, json={"detail": "Not Found"})
        if key not in table:
            return httpx.Response(404, json={"detail": "Not found"})
        return httpx.Response(200, json={"id": int(key), "uid": table[key]})


@pytest.fixture
def skynet():
    return OldSkynet()


@pytest.fixture
def client_for_token(skynet):
    def make(token: str) -> httpx.Client:
        return httpx.Client(
            base_url="http://skynet.test/",
            headers={"Authorization": f"Bearer {token}"},
            transport=httpx.MockTransport(skynet),
        )

    return make


@pytest.fixture
def ada(django_user_model):
    user = django_user_model.objects.create_user(username="ada", password="x" * 16)
    SkynetProfile.objects.create(user=user, api_token="ada-token")
    return user


@pytest.fixture
def target():
    return Target.objects.create(name="AT 2026abc", type=Target.SIDEREAL, ra=1, dec=2)


def record(owner, target, observation_id, **parameters):
    return ObservationRecord.objects.create(
        target=target,
        user=owner,
        facility="Skynet",
        observation_id=observation_id,
        parameters=parameters,
    )


def test_integer_ids_become_uids_through_the_owners_token(
    ada, target, skynet, client_for_token
):
    old = record(ada, target, "317", grant=99, skynet_owner="Ada Lovelace (user)")
    assert migrate_records(apps, client_for_token) == 1
    old.refresh_from_db()
    assert old.observation_id == OBSERVATION_UID
    assert old.parameters == {"grant": GRANT_UID, "skynet_owner": "Ada Lovelace (user)"}
    assert {r.headers["Authorization"] for r in skynet.requests} == {"Bearer ada-token"}
    assert [r.url.path for r in skynet.requests] == [
        "/v1/observations/317",
        "/v1/observing-grants/99",
    ]


def test_records_already_on_uids_and_other_facilities_are_left_alone(
    ada, target, skynet, client_for_token
):
    done = record(ada, target, OBSERVATION_UID, grant=GRANT_UID)
    lco = ObservationRecord.objects.create(
        target=target, user=ada, facility="LCO", observation_id="123", parameters={}
    )
    assert migrate_records(apps, client_for_token) == 0
    done.refresh_from_db()
    lco.refresh_from_db()
    assert done.observation_id == OBSERVATION_UID and done.parameters == {
        "grant": GRANT_UID
    }
    assert lco.observation_id == "123"
    assert skynet.requests == []


def test_a_grant_stored_as_a_digit_string_is_rewritten_too(
    ada, target, skynet, client_for_token
):
    old = record(ada, target, OBSERVATION_UID, grant="99")
    assert migrate_records(apps, client_for_token) == 1
    old.refresh_from_db()
    assert old.parameters["grant"] == GRANT_UID
    assert [r.url.path for r in skynet.requests] == ["/v1/observing-grants/99"]


def test_an_unresolvable_record_fails_the_migration_naming_it(
    ada, target, skynet, client_for_token
):
    lost = record(ada, target, "999", grant=99)
    fine = record(ada, target, "317", grant=99)
    with pytest.raises(
        UidMigrationError, match=rf"record {lost.pk} \(observation 999\).*404"
    ):
        migrate_records(apps, client_for_token)
    # Nothing is skipped silently: the fine record was still rewritten, and the
    # failure names the one to fix before running migrate again.
    fine.refresh_from_db()
    assert fine.observation_id == OBSERVATION_UID


def test_a_record_whose_owner_has_no_token_fails_the_migration(
    django_user_model, target, skynet, client_for_token
):
    bob = django_user_model.objects.create_user(username="bob")
    missing = record(bob, target, "317", grant=99)
    with pytest.raises(
        UidMigrationError, match=rf"record {missing.pk} .*no Skynet API token"
    ):
        migrate_records(apps, client_for_token)
    assert skynet.requests == []


def test_the_shared_facility_token_is_the_fallback(
    django_user_model, target, skynet, client_for_token, settings
):
    settings.FACILITIES = {"SKYNET": {"api_token": "shared-token"}}
    bob = django_user_model.objects.create_user(username="bob")
    old = record(bob, target, "317", grant=99)
    assert migrate_records(apps, client_for_token) == 1
    old.refresh_from_db()
    assert old.observation_id == OBSERVATION_UID
    assert {r.headers["Authorization"] for r in skynet.requests} == {
        "Bearer shared-token"
    }


def test_an_answer_without_a_uid_is_refused(ada, target, client_for_token, monkeypatch):
    def no_uid(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 317})

    def make(token: str) -> httpx.Client:
        return httpx.Client(
            base_url="http://skynet.test/", transport=httpx.MockTransport(no_uid)
        )

    record(ada, target, "317", grant=99)
    with pytest.raises(UidMigrationError, match="carries no uid"):
        migrate_records(apps, make)


def test_the_migration_file_runs_the_rewrite(ada, target, skynet, monkeypatch):
    """0004 calls migrate_records against Skynet; here Skynet is the mock"""
    import importlib

    import tom_skynet.uid_migration as module

    real_client = httpx.Client

    def mocked(**kwargs):
        # The migration's own client, pointed at the mock Skynet
        return real_client(transport=httpx.MockTransport(skynet), **kwargs)

    monkeypatch.setattr(module.httpx, "Client", mocked)
    migration = importlib.import_module("tom_skynet.migrations.0004_skynet_uids")
    old = record(ada, target, "317", grant=99)
    migration.forwards(apps, None)
    old.refresh_from_db()
    assert (
        old.observation_id == OBSERVATION_UID and old.parameters["grant"] == GRANT_UID
    )
    assert {r.headers["Authorization"] for r in skynet.requests} == {"Bearer ada-token"}
