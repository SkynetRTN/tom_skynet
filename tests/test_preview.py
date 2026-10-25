from datetime import UTC, datetime

from aeonlib.models import SiderealTarget, Window
from aeonlib.skynet import (
    FilterExposure,
    InstrumentPreview,
    PlanTotals,
    SkynetImagingRequest,
)
from aeonlib.skynet.pools import Telescope

from tom_skynet.preview import preview_refusal, preview_summary


def telescope(name: str) -> Telescope:
    return Telescope(
        id=1,
        name=name,
        slug=name.lower(),
        latitude_deg=0,
        longitude_deg=0,
        elevation_m=0,
    )


def request() -> SkynetImagingRequest:
    return SkynetImagingRequest(
        name="x",
        grant_id=1,
        target=SiderealTarget(name="t", type="ICRS", ra=1, dec=2),
        window=Window(
            start=datetime(2026, 11, 1, tzinfo=UTC),
            end=datetime(2026, 11, 2, tzinfo=UTC),
        ),
        exposures=[FilterExposure(filter_id="V", total_exposure_time=60)],
    )


def preview(instrument_id: int, name: str, *, compatible=True, observable=True):
    return InstrumentPreview(
        instrument_id=instrument_id,
        telescope=telescope(name),
        compatible=compatible,
        reasons=[] if compatible else ["No filter V"],
        observable=observable if compatible else None,
        checked_until=datetime(2026, 11, 2, tzinfo=UTC) if compatible else None,
        plan=PlanTotals(total_exposures=1, total_exposure_time=60.0)
        if compatible
        else None,
    )


def test_a_telescope_with_two_instruments_counts_once():
    previews = [
        preview(1, "Karoo"),
        preview(2, "Karoo", compatible=False),
        preview(3, "Outback", observable=False),
    ]
    assert preview_summary(previews, request()) == (
        "Skynet accepts this request. It can run on 1 of 2 telescopes (Karoo), "
        "with an estimated 60 s of exposure in 1 exposures. Not observable in the "
        "window: Outback."
    )


def test_refusals():
    assert preview_refusal([preview(1, "Karoo", compatible=False)], request()) == (
        "Skynet accepts this request, but no telescope on this time account can "
        "carry it out: No filter V"
    )
    refusal = preview_refusal([preview(1, "Karoo", observable=False)], request())
    assert refusal is not None and "observe it in the window" in refusal
    assert preview_refusal([preview(1, "Karoo")], request()) is None
