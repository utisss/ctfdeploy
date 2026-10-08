from dataclasses import dataclass
from datetime import datetime, timedelta

from ctfdeploy.model import Event

LEAD_TIME = timedelta(days=1)


@dataclass(frozen=True)
class DesiredEvents:
    events: tuple[tuple[Event, bool], ...]
    """Events whose challenges should be up, current first, each with its visibility."""
    config: Event
    """The event CTFd's name, times and homepage should describe."""


def desired_events(events: tuple[Event, ...], now: datetime) -> DesiredEvents:
    """Which events are up at `now`. `events` must be sorted by start."""
    i = next((i for i, e in enumerate(events) if e.end is None or e.end > now), len(events) - 1)
    current = events[i]
    previous = events[i - 1] if i > 0 else None
    switched = now >= current.start - LEAD_TIME

    up = [current] if switched or previous is None else [current, previous]
    return DesiredEvents(
        events=tuple((e, now >= e.start) for e in up),
        config=current if switched or previous is None else previous,
    )
