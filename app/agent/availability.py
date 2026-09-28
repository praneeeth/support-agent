"""Which rooms are free: the vertical's own closed dates, plus the property's iCal feeds if set.

Answers "is it free", never "it's yours". A result is a snapshot for the customer to enquire
about; only the owner confirms a stay.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from app.config import get_settings
from app.integrations.base import Credentials
from app.integrations.ical import ICalAvailability
from app.verticals.config import Availability, Room


class InvalidStay(ValueError):
    """A request no property could answer (past dates, too short, too far ahead)."""


class Calendar(Protocol):
    def busy(self) -> list[tuple[date, date]]: ...


class _ICalCalendar:
    def __init__(self, urls: str) -> None:
        self._source = ICalAvailability(Credentials({"urls": urls}))

    def busy(self) -> list[tuple[date, date]]:
        """Raises ConnectorError when a feed can't be fetched."""
        return [(b.start, b.end) for b in self._source.busy_periods()]


_calendars: dict[str, _ICalCalendar] = {}


def _ical_urls() -> str:
    return get_settings().ical_urls


def _calendar() -> Calendar | None:
    urls = _ical_urls().strip()
    if not urls:
        return None
    if urls not in _calendars:
        _calendars[urls] = _ICalCalendar(urls)  # kept, so its cache outlives one request
    return _calendars[urls]


@dataclass(frozen=True)
class RoomAnswer:
    room: Room
    free: bool


def validate_stay(config: Availability, check_in: date, check_out: date, today: date) -> int:
    """Nights in the stay, or InvalidStay saying what to ask the customer."""
    if check_in < today:
        raise InvalidStay("The check-in date is in the past. Ask the customer for new dates.")
    if check_out <= check_in:
        raise InvalidStay("The check-out date must be after the check-in date. Ask again.")
    nights = (check_out - check_in).days
    if nights < config.min_nights:
        raise InvalidStay(f"The minimum stay is {config.min_nights} nights.")
    if nights > config.max_nights:
        raise InvalidStay(
            f"The maximum stay is {config.max_nights} nights; longer stays need the owner."
        )
    if check_in > today + timedelta(days=config.horizon_days):
        raise InvalidStay(
            f"Those dates are too far ahead: availability is only known {config.horizon_days} "
            "days out. Offer to pass on an enquiry instead."
        )
    return nights


def check(
    config: Availability, check_in: date, check_out: date, guests: int, today: date
) -> list[RoomAnswer]:
    """Rooms that sleep the party, each free or not. Raises ConnectorError if a feed is down."""
    validate_stay(config, check_in, check_out, today)
    calendar = _calendar()
    whole_property = calendar.busy() if calendar is not None else []

    def overlaps(start: date, end: date) -> bool:
        return start < check_out and check_in < end

    answers = []
    for room in config.rooms:
        if room.sleeps < guests:
            continue
        taken = any(overlaps(s, e) for s, e in whole_property) or any(
            overlaps(b.start, b.end) for b in config.blocked if b.room in ("", room.name)
        )
        answers.append(RoomAnswer(room, free=not taken))
    return answers
