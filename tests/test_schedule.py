from datetime import UTC, datetime, timedelta
from pathlib import Path

from ctfdeploy.model import Event
from ctfdeploy.schedule import desired_events

DAY = timedelta(days=1)
T0 = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)


def event(name: str, start: datetime, hours: int | None = 2) -> Event:
    end = start + timedelta(hours=hours) if hours else None
    return Event(name, start, end, Path(name))


SEPT = event("sept", T0 - 14 * DAY)
OCT = event("oct", T0)
NOV = event("nov", T0 + 14 * DAY)
EVENTS = (SEPT, OCT, NOV)


def names(now: datetime) -> list[tuple[str, bool]]:
    return [(e.name, visible) for e, visible in desired_events(EVENTS, now).events]


def test_between_events_keeps_previous_up_and_next_hidden():
    assert names(T0 - 7 * DAY) == [("oct", False), ("sept", True)]


def test_previous_comes_down_a_day_before_next_starts():
    assert names(T0 - DAY + timedelta(minutes=1)) == [("oct", False)]


def test_challenges_become_visible_at_start():
    assert names(T0) == [("oct", True)]


def test_ended_event_stays_until_next_lead_time():
    assert names(T0 + 3 * DAY) == [("nov", False), ("oct", True)]


def test_last_event_stays_up_after_it_ends():
    assert names(NOV.end + 30 * DAY) == [("nov", True)]


def test_before_first_event_only_it_is_up_hidden():
    assert names(SEPT.start - 30 * DAY) == [("sept", False)]


def test_config_switches_to_current_at_lead_time():
    assert desired_events(EVENTS, T0 - 2 * DAY).config.name == "sept"
    assert desired_events(EVENTS, T0 - DAY).config.name == "oct"


def test_open_ended_event_is_always_current():
    forever = event("forever", T0 - 365 * DAY, hours=None)
    plan = desired_events((forever,), T0)
    assert [(e.name, v) for e, v in plan.events] == [("forever", True)]
    assert plan.config is forever
