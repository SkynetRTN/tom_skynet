import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import requests
from aeonlib.skynet import SkynetFacility as AeonlibSkynetFacility

DATA = Path(__file__).parent / "data"

#: The recorded fixtures' uids (instrument_pools.json): Ada owns an account
#: reaching the three simulators, and holds a member grant on the Transient
#: team organization's account, which reaches one simulator and PROMPT-CTIO-6.
#: Skynet addresses everything by uid; its integer ids are never read.
ADA_UID = "50484ebe-5f22-4e78-b71f-0743cfcfc8a7"
TEAM_UID = "55305e4c-005e-4cab-928f-e1e2ed1d70f2"
BOB_UID = "b0b00000-0000-4000-8000-000000000135"
ADA_GRANT = "dd9463a0-b43e-4d0f-8def-90c688558d3c"
TEAM_GRANT = "b394f88c-73b5-4bf2-ae29-fa2c1d222c98"
NO_SUCH_GRANT = "00000000-0000-4000-8000-000000000000"
ADA_ACCOUNT = "839163a6-4dc9-4e29-b60d-f6126399440e"
TEAM_ACCOUNT = "5730745b-e7ae-43e8-acb4-74ac43f00af5"
ATACAMA = "746670bb-4e27-4acb-a307-b6275471020f"
KAROO = "6a94483d-cfce-428f-be47-430dfb7817fd"
OUTBACK = "45265f1b-4b6e-4cb0-9151-336131ee6bcd"
SIMS = [ATACAMA, KAROO, OUTBACK]
#: The observation :publish answers with
PUBLISHED = "5b1d0c3e-0000-4000-8000-000000000310"
#: GET /v1/me for the fixtures' user
ADA = {"uid": ADA_UID, "entityType": "user", "username": "ada", "name": "Ada Lovelace"}
#: The fixtures' request window opens on 2026-11-01; the facility previews it
#: from before that, whatever today is
NOW = datetime(2026, 10, 25, tzinfo=UTC)


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class SkynetAPI:
    """Skynet's API for AEONlib, answering from the recorded fixtures"""

    def __init__(self):
        self.pools = load("instrument_pools.json")
        self.progress: dict[str, dict] = {}
        self.publish: tuple[int, dict] = (201, {"uid": PUBLISHED})
        self.requests: list[httpx.Request] = []
        self.downloads: list[str] = []
        self.download_status = 200
        #: The bytes every presigned download answers with
        self.download_content = b"SIMPLE  =                    T"
        #: File ids the presign endpoint leaves out of its answer
        self.unsigned: set[str] = set()
        #: The answer to a cancel; a 2xx marks the observation canceled
        self.cancel_status = 200
        #: Tokens Skynet answers with a 401
        self.rejected_tokens: set[str] = set()
        #: The answer to :validate
        self.validate: tuple[int, dict] = (200, {})
        #: Instruments the preview finds incompatible, or with no observable window
        self.incompatible: set[str] = set()
        self.unobservable: set[str] = set()
        #: (total exposures, total exposure time) per instrument
        self.plans: dict[str, tuple[int, float]] = {
            ATACAMA: (6, 12.0),
            KAROO: (6, 15.5),
            OUTBACK: (6, 20.0),
        }

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token in self.rejected_tokens:
            return httpx.Response(401, json={"detail": "Invalid token"})
        if path == "/v1/me":
            return httpx.Response(200, json=ADA)
        if path == "/v1/me/observing-access/instrument-pools":
            return httpx.Response(200, json=self.pools)
        if path == "/v1/observation-specs:publish":
            status, body = self.publish
            return httpx.Response(status, json=body)
        if path == "/v1/observation-specs:validate":
            status, body = self.validate
            return httpx.Response(status, json=body)
        if path == "/v1/observation-specs:compatible-instruments":
            ok = load("dry_run/compatible_instruments.json")["results"][0]
            refused = load("dry_run/compatible_instruments_incompatible.json")
            refused = refused["results"][0]
            results = [
                {**(refused if i in self.incompatible else ok), "instrumentUid": i}
                for i in json.loads(request.content)["instrumentUids"]
            ]
            return httpx.Response(200, json={"results": results})
        if path == "/v1/observation-specs:observability":
            # The recorded windows are all unobservable; open one
            instrument_uid = json.loads(request.content)["instrumentUid"]
            body = load("dry_run/observability.json")
            if instrument_uid not in self.unobservable:
                body["windows"][0] |= {"observable": True, "reason": None}
            return httpx.Response(200, json=body)
        if path == "/v1/observation-specs:plan":
            count, seconds = self.plans[json.loads(request.content)["instrumentUid"]]
            body = copy.deepcopy(load("dry_run/plan.json"))
            body["opticalImagingConfigurationPlan"] |= {
                "totalExposures": count,
                "totalExposureTimeSec": seconds,
            }
            return httpx.Response(200, json=body)
        if path == "/v1/files/data/presign":
            ids = json.loads(request.content)["fileIds"]
            items = [
                {"fileId": i, "url": f"https://minio.invalid/{i}"}
                for i in ids
                if i not in self.unsigned
            ]
            return httpx.Response(200, json={"expiresIn": 900, "items": items})
        observation_uid = path.removeprefix("/v1/observations/").removesuffix(
            "/progress"
        )
        if request.method == "PATCH" and observation_uid in self.progress:
            if self.cancel_status >= 400:
                return httpx.Response(
                    self.cancel_status, json={"detail": "Observation can't be canceled"}
                )
            self.progress[observation_uid] = self.progress[observation_uid] | {
                "state": "canceled",
                "status": "canceled",
                "productsFinal": True,
            }
            return httpx.Response(200, json={"uid": observation_uid})
        if observation_uid in self.progress:
            return httpx.Response(200, json=self.progress[observation_uid])
        return httpx.Response(404, json={"detail": "Observation not found"})


@pytest.fixture
def skynet_api(monkeypatch, settings, tmp_path):
    """
    Serve AEONlib from SkynetAPI, and the presigned downloads from memory, so
    the facility runs end to end without a network
    """
    api = SkynetAPI()
    monkeypatch.setattr("aeonlib.skynet.facility._now", lambda: NOW)
    settings.FACILITIES = {
        "SKYNET": {"api_root": "http://skynet.test/", "web_root": "http://web.test/"}
    }
    settings.MEDIA_ROOT = tmp_path

    def facility(settings):
        client = httpx.Client(
            base_url=settings.skynet_api_root, transport=httpx.MockTransport(api.handle)
        )
        return AeonlibSkynetFacility(settings=settings, client=client)

    monkeypatch.setattr("tom_skynet.client.AeonlibSkynetFacility", facility)

    def download(url, *args, **kwargs):
        api.downloads.append(url)
        response = httpx.Response(
            api.download_status,
            content=api.download_content,
            request=httpx.Request("GET", url),
        )

        def raise_for_status():
            if response.is_error:
                raise requests.HTTPError(f"{response.status_code} for {url}")

        return SimpleNamespace(
            content=response.content, raise_for_status=raise_for_status
        )

    monkeypatch.setattr("tom_skynet.facility.requests.get", download)
    return api
