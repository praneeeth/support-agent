from datetime import date, timedelta

import httpx
import pytest

from app.integrations.base import ConnectorError, Credentials, Status
from app.integrations.ical import ICalAvailability, parse_busy

FEED = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261001
DTEND;VALUE=DATE:20261005
SUMMARY:Reserved - Airbnb
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261010
DTEND;VALUE=DATE:20261012
SUMMARY:Blocked
END:VEVENT
END:VCALENDAR"""


def _connector(handler=None, urls: str = "https://airbnb.example/cal.ics") -> ICalAvailability:
    handler = handler or (lambda r: httpx.Response(200, text=FEED))
    return ICalAvailability(
        Credentials({"urls": urls, "listing": "Seaside"}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_parse_busy_periods() -> None:
    periods = parse_busy(FEED)
    assert len(periods) == 2
    assert periods[0].start == date(2026, 10, 1) and periods[0].end == date(2026, 10, 5)


def test_parse_tolerates_junk_and_missing_dtend() -> None:
    ics = (
        "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20261101\nEND:VEVENT\nBEGIN:VEVENT\nnonsense\nEND:VEVENT"
    )
    periods = parse_busy(ics)
    assert len(periods) == 1
    assert periods[0].end == date(2026, 11, 2)  # one night assumed


def test_not_configured() -> None:
    c = ICalAvailability(Credentials({}))
    assert c.health().status is Status.not_configured
    assert c.health().usable is False


def test_availability_checks() -> None:
    c = _connector()
    assert c.is_available(date(2026, 10, 6), date(2026, 10, 9)) is True
    assert c.is_available(date(2026, 10, 2), date(2026, 10, 3)) is False
    assert c.is_available(date(2026, 9, 30), date(2026, 10, 2)) is False  # overlaps the start
    assert c.is_available(date(2026, 10, 5), date(2026, 10, 7)) is True  # DTEND is exclusive


def test_check_out_must_follow_check_in() -> None:
    with pytest.raises(ValueError, match="after"):
        _connector().is_available(date(2026, 10, 5), date(2026, 10, 5))


def test_two_calendars_are_merged() -> None:
    other = FEED.replace("20261010", "20261020").replace("20261012", "20261022")

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=FEED if "airbnb" in str(request.url) else other)

    c = _connector(handle, urls="https://airbnb.example/a.ics,https://booking.example/b.ics")
    assert len(c.busy_periods()) == 4
    assert c.is_available(date(2026, 10, 20), date(2026, 10, 21)) is False


def test_next_free_window_skips_busy_dates() -> None:
    today = date.today()
    busy = f"""BEGIN:VEVENT
DTSTART;VALUE=DATE:{today:%Y%m%d}
DTEND;VALUE=DATE:{today + timedelta(days=3):%Y%m%d}
END:VEVENT"""
    c = _connector(lambda r: httpx.Response(200, text=busy))
    window = c.next_free_window(nights=2)
    assert window is not None and window[0] >= today + timedelta(days=3)


def test_fetch_failure_is_a_connector_error() -> None:
    c = _connector(lambda r: httpx.Response(404))
    with pytest.raises(ConnectorError):
        c.busy_periods()
    assert c.health().status is Status.degraded
