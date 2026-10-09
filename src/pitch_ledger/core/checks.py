"""One line of a setup check (``check-models``, ``check-store``): PASS/FAIL with a detail."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def short_error(exc: BaseException) -> str:
    """Exception type and first message line, capped, for a check detail."""
    line = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {line}"[:300]
