from datetime import UTC

from aeonlib.skynet import InstrumentPreview, SkynetImagingRequest


def _name(preview: InstrumentPreview) -> str:
    if preview.telescope is not None:
        return preview.telescope.name
    return f"instrument {preview.instrument_id}"


def _names(previews: list[InstrumentPreview]) -> str:
    return ", ".join(sorted(_name(p) for p in previews))


def _span(low: float, high: float, unit: str = "") -> str:
    if round(low, 1) == round(high, 1):
        return f"{_number(low)}{unit}"
    return f"{_number(low)}–{_number(high)}{unit}"


def _number(value: float) -> str:
    return f"{value:.3g}" if value < 100 else f"{value:.0f}"


def _seconds(low: float, high: float) -> str:
    if high < 120:
        return _span(low, high, " s")
    if high < 7200:
        return _span(low / 60, high / 60, " min")
    return _span(low / 3600, high / 3600, " h")


def _window_end(request: SkynetImagingRequest):
    return request.window.end.utc.to_datetime(timezone=UTC)


def _partly_checked(
    previews: list[InstrumentPreview], request: SkynetImagingRequest
) -> bool:
    end = _window_end(request)
    return any(
        p.checked_until is not None and p.checked_until < end
        for p in previews
        if p.compatible
    )


def preview_summary(
    previews: list[InstrumentPreview], request: SkynetImagingRequest
) -> str:
    """The Validate message: which telescopes can carry out the request, and the estimate"""
    compatible = [p for p in previews if p.compatible]
    usable = [p for p in compatible if p.observable]
    unobservable = [p for p in compatible if not p.observable]
    incompatible = [p for p in previews if not p.compatible]
    parts = ["Skynet accepts this request."]
    plans = [p.plan for p in usable if p.plan is not None]
    if usable:
        sentence = f"It can run on {len(usable)} of {len(previews)} telescopes ({_names(usable)})"
        if plans:
            times = [plan.total_exposure_time for plan in plans]
            counts = [plan.total_exposures for plan in plans]
            sentence += (
                f", with an estimated {_seconds(min(times), max(times))} of exposure"
                f" in {_span(min(counts), max(counts))} exposures"
            )
        parts.append(sentence + ".")
    else:
        parts.append("No telescope on this time account can run it in the window.")
    if unobservable:
        parts.append(f"Not observable in the window: {_names(unobservable)}.")
    if incompatible:
        reasons = sorted({r for p in incompatible for r in p.reasons})
        parts.append(
            f"Can't carry it out: {_names(incompatible)}"
            + (f" ({'; '.join(reasons)})" if reasons else "")
            + "."
        )
    if _partly_checked(previews, request):
        checked = min(
            p.checked_until for p in compatible if p.checked_until is not None
        )
        parts.append(
            f"Observability was checked up to {checked:%Y-%m-%d %H:%M} UTC only."
        )
    return " ".join(parts)


def preview_refusal(
    previews: list[InstrumentPreview], request: SkynetImagingRequest
) -> str | None:
    """
    Why Submit refuses the request, or None. A request no telescope can carry
    out, or none can observe in a window checked to its end, could only expire.
    """
    compatible = [p for p in previews if p.compatible]
    if not compatible:
        reasons = sorted({r for p in previews for r in p.reasons})
        detail = f": {'; '.join(reasons)}" if reasons else "."
        return (
            "Skynet accepts this request, but no telescope on this time account "
            f"can carry it out{detail}"
        )
    if any(p.observable for p in compatible) or _partly_checked(previews, request):
        return None
    return (
        "Skynet accepts this request, but no telescope on this time account can "
        "observe it in the window. Widen the window or relax the constraints."
    )
