"""
Rewrite the Skynet integer ids stored before Skynet's uid contract.

Skynet is retiring its integer ids (its C2 release blocker): every resource is
addressed by uid, and the integer paths stop resolving at Skynet's contract
deploy. tom_skynet 0.4 stores the observation uid as
ObservationRecord.observation_id and the grant uid in the record's parameters;
records made by earlier versions hold the integers. Migration 0004 runs this
against Skynet's API, which still accepts both, so it must run before Skynet's
contract deploy. Skynet tracks that on its release gate (UID_MIGRATION_DESIGN
D-10). Each record is read with its owner's token (the token that made it, as
every other tom_skynet call); a record this can't resolve fails the migration
rather than being skipped, so nothing is left behind unnoticed.
"""

import logging
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

FACILITY_NAME = "Skynet"


class UidMigrationError(Exception):
    """A record whose Skynet ids couldn't be rewritten"""


def is_integer_id(value) -> bool:
    return isinstance(value, str) and value.isdigit()


def migrate_records(
    apps, client_for_token: Callable[[str], httpx.Client] | None = None
) -> int:
    """
    Rewrite every Skynet record whose observation_id is an integer string, and the
    integer grant in its parameters, to their uids. Returns the number rewritten.
    apps is the migration state's app registry (or django.apps.apps).
    """
    from .credentials import settings_for_token as _settings_for_token

    ObservationRecord = apps.get_model("tom_observations", "ObservationRecord")
    SkynetProfile = apps.get_model("tom_skynet", "SkynetProfile")

    def default_client(token: str) -> httpx.Client:
        settings = _settings_for_token(token)
        return httpx.Client(
            base_url=settings.skynet_api_root,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30.0,
        )

    make_client = client_for_token or default_client
    records = ObservationRecord.objects.filter(facility=FACILITY_NAME).order_by("pk")
    rewritten = 0
    problems: list[str] = []
    for record in records:
        parameters = record.parameters if isinstance(record.parameters, dict) else {}
        grant = parameters.get("grant")
        old_observation = record.observation_id
        needs_observation = is_integer_id(old_observation)
        needs_grant = isinstance(grant, int) or is_integer_id(grant)
        if not (needs_observation or needs_grant):
            continue
        token = _token_for(record, SkynetProfile)
        if token is None:
            problems.append(
                f"record {record.pk} (observation {old_observation}): no Skynet API "
                "token for its owner; set one with skynet_set_token, or delete the record"
            )
            continue
        with make_client(token) as client:
            try:
                if needs_observation:
                    record.observation_id = _uid(
                        client, f"v1/observations/{old_observation}"
                    )
                if needs_grant:
                    parameters["grant"] = _uid(client, f"v1/observing-grants/{grant}")
                    record.parameters = parameters
            except UidMigrationError as exc:
                problems.append(
                    f"record {record.pk} (observation {old_observation}): {exc}"
                )
                continue
        record.save(update_fields=["observation_id", "parameters"])
        logger.info(
            "Skynet record %s: observation %s -> %s",
            record.pk,
            old_observation,
            record.observation_id,
        )
        rewritten += 1
    if problems:
        raise UidMigrationError(
            "Skynet records whose integer ids couldn't be rewritten to uids "
            "(Skynet's integer ids stop resolving at its contract deploy):\n  "
            + "\n  ".join(problems)
        )
    return rewritten


def _token_for(record, SkynetProfile) -> str | None:
    """The record owner's token, else the shared facility token, else None"""
    from django.conf import settings as django_settings

    token = None
    if record.user_id is not None:
        profile = SkynetProfile.objects.filter(user_id=record.user_id).first()
        token = profile.api_token if profile is not None else None
    return (
        token
        or getattr(django_settings, "FACILITIES", {}).get("SKYNET", {}).get("api_token")
        or None
    )


def _uid(client: httpx.Client, path: str) -> str:
    try:
        response = client.get(path)
    except httpx.TransportError as exc:
        raise UidMigrationError(f"Skynet unreachable reading {path}: {exc}") from exc
    if response.is_error:
        raise UidMigrationError(
            f"Skynet answered {response.status_code} for {path}: {response.text[:200]}"
        )
    body = response.json()
    uid = body.get("uid") if isinstance(body, dict) else None
    if not isinstance(uid, str) or not uid:
        raise UidMigrationError(f"Skynet's answer for {path} carries no uid")
    return uid
