"""Availability from iCal feeds — the PMS-free route for small properties.

Airbnb, Booking.com and Vrbo each publish a per-listing .ics link the owner can copy in one click.
Busy dates come from those feeds; everything else is free. No partnership, no channel manager.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from app.agent.llm import ToolSchema
from app.integrations.base import Cache, ConnectorError, Credentials, Health, Status, registry

log = logging.getLogger(__name__)

FIELDS = ("urls", "listing")
_DATE = re.compile(r"^(DTSTART|DTEND)(?:;VALUE=DATE)?(?:;[^:]*)?:(\d{8})", re.MULTILINE)


@dataclass(frozen=True)
class Busy:
    start: date
    end: date  # exclusive, as iCal DTEND is for all-day events

    def overlaps(self, check_in: date, check_out: date) -> bool:
        return self.start < check_out and check_in < self.end


class ICalAvailability:
    name = "ical_availability"

    def __init__(self, credentials: Credentials, client: httpx.Client | None = None) -> None:
        self.credentials = credentials
        self._client = client
        self._cache = Cache(ttl_seconds=600)  # calendars change slowly
        self._last_error = ""

    @property
    def urls(self) -> list[str]:
        raw = self.credentials.get("urls")
        return [u.strip() for u in raw.split(",") if u.strip()]

    def health(self) -> Health:
        if not self.urls:
            return Health(Status.not_configured, "Set ICAL_URLS to the listing's calendar links")
        if self._last_error:
            return Health(Status.degraded, self._last_error)
        return Health(Status.ok, f"{len(self.urls)} calendar(s) connected")

    def tools(self) -> list[ToolSchema]:
        return [
            {
                "name": "check_availability",
                "description": (
                    "Check whether the property is free between two dates. Returns available or "
                    "not available — never confirms a booking."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "check_in": {"type": "string", "description": "YYYY-MM-DD"},
                        "check_out": {"type": "string", "description": "YYYY-MM-DD"},
                    },
                    "required": ["check_in", "check_out"],
                },
            }
        ]

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=10.0, follow_redirects=True)
        return self._client

    def busy_periods(self) -> list[Busy]:
        cached = self._cache.get("busy")
        if isinstance(cached, list):
            return cached
        if not self.urls:
            raise ConnectorError("No calendars configured")
        periods: list[Busy] = []
        for url in self.urls:
            try:
                response = self._http().get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                self._last_error = f"{exc.__class__.__name__} fetching a calendar"
                log.warning("iCal fetch failed: %s", self._last_error)
                raise ConnectorError(self._last_error) from exc
            periods.extend(parse_busy(response.text))
        self._last_error = ""
        self._cache.put("busy", periods)
        return periods

    def is_available(self, check_in: date, check_out: date) -> bool:
        if check_out <= check_in:
            raise ValueError("check_out must be after check_in")
        return not any(b.overlaps(check_in, check_out) for b in self.busy_periods())

    def next_free_window(self, nights: int, within_days: int = 60) -> tuple[date, date] | None:
        """Used when the dates asked for are taken, so the bot can offer something."""
        busy = self.busy_periods()
        start = date.today()
        for offset in range(within_days):
            check_in = start + timedelta(days=offset)
            check_out = check_in + timedelta(days=nights)
            if not any(b.overlaps(check_in, check_out) for b in busy):
                return check_in, check_out
        return None


def parse_busy(ics: str) -> list[Busy]:
    """Pull DTSTART/DTEND pairs out of a calendar. Tolerant by design — feeds vary."""
    periods: list[Busy] = []
    for block in ics.split("BEGIN:VEVENT")[1:]:
        dates = {kind: value for kind, value in _DATE.findall(block)}
        if "DTSTART" not in dates:
            continue
        try:
            start = datetime.strptime(dates["DTSTART"], "%Y%m%d").date()
            end = (
                datetime.strptime(dates["DTEND"], "%Y%m%d").date()
                if "DTEND" in dates
                else start + timedelta(days=1)
            )
        except ValueError:
            continue
        if end > start:
            periods.append(Busy(start, end))
    return periods


registry.register("ical_availability", lambda creds: ICalAvailability(creds))
