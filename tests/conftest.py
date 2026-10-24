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

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/v1/me/observing-access/instrument-pools":
            return httpx.Response(200, json=self.pools)
        if path == "/v1/observation-specs:publish":
            status, body = self.publish
            return httpx.Response(status, json=body)
        if path == "/v1/files/data/presign":
            ids = json.loads(request.content)["fileIds"]
            items = [{"fileId": i, "url": f"https://minio.invalid/{i}"} for i in ids]
            return httpx.Response(200, json={"expiresIn": 900, "items": items})
        observation_id = path.removeprefix("/v1/observations/").removesuffix(
            "/progress"
        )
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
