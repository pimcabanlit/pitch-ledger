"""Match rules: checks over a whole match (MATCH-01; MATCH-02 lives in ``realism``)."""

from collections import Counter
from collections.abc import Iterable

from pitch_ledger.schema import Event
from pitch_ledger.schema.enums import EventType
from pitch_ledger.validation.results import ValidationResult, violation

PERIODS = (1, 2)


class PeriodMarkerCounter:
    """MATCH-01: exactly one period_start and one period_end per period, start before end."""

    def __init__(self) -> None:
        self.counts: Counter[tuple[int, EventType]] = Counter()
        self.order: list[tuple[int, EventType]] = []

    def add(self, event: Event) -> None:
        if event.event_type in (EventType.PERIOD_START, EventType.PERIOD_END):
            key = (event.period, event.event_type)
            self.counts[key] += 1
            self.order.append(key)

    def results(self) -> list[ValidationResult]:
        out = []
        for period in PERIODS:
            for marker in (EventType.PERIOD_START, EventType.PERIOD_END):
                n = self.counts[(period, marker)]
                if n != 1:
                    msg = f"period {period} has {n} {marker} events, expected 1"
                    out.append(violation("MATCH-01", None, msg))
        expected = [(p, m) for p in PERIODS for m in (EventType.PERIOD_START, EventType.PERIOD_END)]
        if not out and self.order != expected:
            out.append(violation("MATCH-01", None, "period markers out of order"))
        return out


def check_period_markers(events: Iterable[Event]) -> list[ValidationResult]:
    """Run MATCH-01 over a finished match."""
    counter = PeriodMarkerCounter()
    for event in events:
        counter.add(event)
    return counter.results()
