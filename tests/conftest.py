import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import requests
from aeonlib.skynet import SkynetFacility as AeonlibSkynetFacility

DATA = Path(__file__).parent / "data"


def load(name: str):
    return json.loads((DATA / name).read_text())


class SkynetAPI:
    """Skynet's API for AEONlib, answering from the recorded fixtures"""

    def __init__(self):
        self.pools = load("instrument_pools.json")
        self.progress: dict[str, dict] = {}
        self.publish: tuple[int, dict] = (201, {"id": 310, "uid": "obs-310"})
        self.requests: list[httpx.Request] = []
        self.downloads: list[str] = []
        self.download_status = 200
        #: File ids the presign endpoint leaves out of its answer
        self.unsigned: set[str] = set()
        #: The answer to a cancel; a 2xx marks the observation canceled
        self.cancel_status = 200
        #: Tokens Skynet answers with a 401
        self.rejected_tokens: set[str] = set()
        #: The answer to :validate
        self.validate: tuple[int, dict] = (200, {})
        #: Instruments the preview finds incompatible, or with no observable window
        self.incompatible: set[int] = set()
        self.unobservable: set[int] = set()
        #: (total exposures, total exposure time) per instrument
        self.plans: dict[int, tuple[int, float]] = {
            105: (6, 12.0),
            106: (6, 15.5),
            107: (6, 20.0),
        }

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token in self.rejected_tokens:
            return httpx.Response(401, json={"detail": "Invalid token"})
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
                {**(refused if i in self.incompatible else ok), "instrumentId": i}
                for i in json.loads(request.content)["instrumentIds"]
            ]
            return httpx.Response(200, json={"results": results})
        if path == "/v1/observation-specs:observability":
            # The recorded windows are all unobservable; open one
            instrument_id = json.loads(request.content)["instrumentId"]
            body = load("dry_run/observability.json")
            if instrument_id not in self.unobservable:
                body["windows"][0] |= {"observable": True, "reason": None}
            return httpx.Response(200, json=body)
        if path == "/v1/observation-specs:plan":
            count, seconds = self.plans[json.loads(request.content)["instrumentId"]]
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
        observation_id = path.removeprefix("/v1/observations/").removesuffix(
            "/progress"
        )
        if request.method == "PATCH" and observation_id in self.progress:
            if self.cancel_status >= 400:
                return httpx.Response(
                    self.cancel_status, json={"detail": "Observation can't be canceled"}
                )
            self.progress[observation_id] = self.progress[observation_id] | {
                "state": "canceled",
                "status": "canceled",
                "productsFinal": True,
            }
            return httpx.Response(200, json={"id": int(observation_id)})
        if observation_id in self.progress:
            return httpx.Response(200, json=self.progress[observation_id])
        return httpx.Response(404, json={"detail": "Observation not found"})


@pytest.fixture
def skynet_api(monkeypatch, settings, tmp_path):
    """
    Serve AEONlib from SkynetAPI, and the presigned downloads from memory, so
    the facility runs end to end without a network
    """
    api = SkynetAPI()
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
            content=b"SIMPLE  =                    T",
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
