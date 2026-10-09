"""INS-01: every number in an insight's text appears in ``metrics`` and recomputes from evidence.

Numbers are compared at the precision they are written with: "64" matches a metric of
64.3; "6.2" matches 6.2 but not 6.3. The recompute half takes a callback; Phase 3 wires
it to the stats engine, which recomputes each metric from the evidence events.
"""

import re
from collections.abc import Callable, Iterable, Mapping

from pitch_ledger.schema import Insight
from pitch_ledger.validation.results import ValidationResult, violation

Recompute = Callable[[Insight], Mapping[str, float]]

_NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.,])\d+(?:\.\d+)?")


DECIMAL_COMMA_LANGUAGES = frozenset({"es"})
"""Languages that write decimals with a comma (6,2); copy in them uses no thousands separator."""


def extract_numbers(text: str, language: str = "en") -> list[str]:
    """Numbers as written in ``text`` (thousands separators kept). IDs like T02 are skipped.

    For decimal-comma languages, "6,2" is read as 6.2.
    """
    if language.split("-")[0] in DECIMAL_COMMA_LANGUAGES:
        text = re.sub(r"(\d),(\d)", r"\1.\2", text)
    return _NUMBER.findall(text)


def unsupported_numbers(text: str, values: Iterable[float], language: str = "en") -> list[str]:
    """Numbers in ``text`` that match none of ``values`` at their written precision."""
    allowed = list(values)
    return [w for w in extract_numbers(text, language) if not any(_matches(w, v) for v in allowed)]


def _decimals(written: str) -> int:
    return len(written.split(".")[1]) if "." in written else 0


def _matches(written: str, value: float) -> bool:
    shown = float(written.replace(",", ""))
    return round(value, _decimals(written)) == shown


def check_insight(insight: Insight, recompute: Recompute | None = None) -> list[ValidationResult]:
    """Return INS-01 results (empty when the insight may air)."""
    out = []
    text = f"{insight.headline}\n{insight.explanation}"
    for written in unsupported_numbers(text, insight.metrics.values()):
        out.append(violation("INS-01", None, f"{insight.insight_id}: {written} not in metrics"))
    if recompute is not None:
        fresh = recompute(insight)
        for name, stated in insight.metrics.items():
            if name not in fresh:
                msg = f"{insight.insight_id}: {name} cannot be recomputed from evidence"
                out.append(violation("INS-01", None, msg))
                continue
            if not _matches(_written(stated), fresh[name]):
                msg = f"{insight.insight_id}: {name} is {stated} but recomputes to {fresh[name]}"
                out.append(violation("INS-01", None, msg))
    return out


def _written(value: float) -> str:
    """How a stated metric value reads, e.g. 64 -> "64", 6.2 -> "6.2"."""
    return str(value)
