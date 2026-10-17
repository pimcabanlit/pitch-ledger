"""Plain words for the producer console: what an ops alert or audit step means, at a glance.

Alerts and audit records keep the engineers' wording (the system of record). For a producer
during a live match the console shows one headline sentence per alert (what happened), what
to do about it (often nothing), and plain detail lines; the original text sits behind a
toggle. Everything here is read off records that already exist, so it also works for alerts
raised before this module did. Nothing here computes a football number.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from pitch_ledger.ops.alerts import minute_label
from pitch_ledger.schema import AuditRecord, Insight, OpsAlert, Trigger

MODE_LABELS = {
    "casual": "Casual fan",
    "analyst": "Analyst",
    "club_focus": "Club focus",
    "player_focus": "Player focus",
    "single_metric": "Single metric",
}
LANGUAGE_LABELS = {"en": "English", "es": "Spanish", "fil": "Filipino"}
NOTHING_TO_DO = "No action needed."
TELL_ENGINEER = "Tell the engineer on duty."
PRE_WRITTEN = "Pre-written wording uses the same checked numbers, so nothing wrong aired."

# Why a text failed its check, in order of how often a producer will see it.
_REASONS: tuple[tuple[str, str], ...] = (
    (r"budget spent", "the AI call budget was used up"),
    (r"content filter", "the safety filter stopped it"),
    (r"model unavailable|timeout|timed out", "the AI service did not answer"),
    (r"copy has \d+ figures", "too many numbers"),
    (r"ordinals|write minutes like", "minutes in the wrong format"),
    (r"numbers not in metrics|does not recompute|not one of the trigger's metrics",
     "a number that does not match the match data"),
    (r"evidence", "it cited the wrong match events"),
    (r"characters \(max|words \(max|sentences \(max", "too long"),
    (r"cliché", "a cliché"),
    (r"unsupported claim", "a claim we have no data for"),
    (r"repeat", "it repeated itself"),
)  # fmt: skip
_VERSION = re.compile(r"^([a-z_]+)/([a-z]{2,3}): ")


@dataclass(frozen=True)
class Brief:
    headline: str  # one sentence: what happened
    action: str  # what the producer should do, often "No action needed."
    needs_action: bool
    details: list[str] = field(default_factory=list)  # plain supporting lines


def reasons(findings: Iterable[str]) -> list[str]:
    """Plain reasons for check findings, each once, in the order first seen."""
    found: list[str] = []
    parts = (p for f in findings for p in _VERSION.sub("", f).split("; ") if p.strip())
    for part in parts:
        low = part.lower()
        reason = next((r for pattern, r in _REASONS if re.search(pattern, low)),
                      "it broke the house style")  # fmt: skip
        if reason not in found:
            found.append(reason)
    return found


def join(items: Sequence[str]) -> str:
    """["a", "b", "c"] -> "a, b and c"."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def versions(findings: Iterable[str]) -> dict[str, list[str]]:
    """Copy-check findings ("casual/es: ...") -> audience mode label -> language labels."""
    found: dict[str, list[str]] = {}
    for finding in findings:
        if m := _VERSION.match(finding):
            mode = MODE_LABELS.get(m.group(1), m.group(1).replace("_", " "))
            language = LANGUAGE_LABELS.get(m.group(2), m.group(2))
            if language not in found.setdefault(mode, []):
                found[mode].append(language)
    return found


def moment(insight: Insight | None) -> str:
    """ "the 77' momentum shift" (or "an insight" when it is not in the ledger)."""
    if insight is None:
        return "an insight"
    kind = insight.insight_type.value.replace("_", " ")
    return f"the {minute_label(insight.period, insight.clock_ms)} {kind}"


def _number(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' * (n != 1)}"


def _fallback(alert: OpsAlert, insight: Insight | None, audits: Sequence[AuditRecord]) -> Brief:
    what = moment(insight)
    cited = [a for a in audits if a.audit_id in alert.refs and a.action == "fallback"]
    findings = [f for a in cited for f in a.findings] or [alert.message]
    why = reasons(findings)
    if "the AI call budget was used up" in why:
        return Brief(f"The AI call budget is used up, so {what} aired with pre-written wording.",
                     "No action needed now. Ask the engineer on duty to raise the budget after "
                     "the match if you want AI wording back.", False,
                     [PRE_WRITTEN])  # fmt: skip
    if "the safety filter stopped it" in why:
        return Brief(f"The safety filter stopped the AI's wording for {what}, so pre-written "
                     "wording aired.", NOTHING_TO_DO, False, [PRE_WRITTEN])  # fmt: skip
    per_mode = versions(findings)
    count = sum(len(langs) for langs in per_mode.values())
    if per_mode:
        lead = f"{_plural(count, 'version')} of {what} aired with pre-written wording."
    else:
        lead = f"{what[0].upper()}{what[1:]} aired with pre-written wording."
    details = [f"Why: the AI's text had {join(why)}."] if why else []
    if per_mode:
        details.append(
            f"Affected: {'; '.join(f'{m} in {join(langs)}' for m, langs in per_mode.items())}."
        )
    return Brief(lead, "No action needed: the numbers on air are correct.", False, details)


def explain_alert(
    alert: OpsAlert,
    insight: Insight | None = None,
    audits: Sequence[AuditRecord] = (),
    trigger: Trigger | None = None,
) -> Brief:
    """The producer's view of one alert (``insight``, ``audits`` and ``trigger``: the records
    its refs point to, when the ledger has them)."""
    text = alert.message
    match alert.kind:
        case "model_fallback":
            return _fallback(alert, insight, audits)
        case "insight_blocked":
            blocks = [a for a in audits if a.action == "block"]
            why = reasons(blocks[-1].findings if blocks else [text.split(": ", 1)[-1]])
            because = f" because of {join(why)}" if why else ""
            return Brief(
                f"The Auditor stopped {moment(insight)}{because}. It will not air.",
                f"{NOTHING_TO_DO} Open the insight to see the details.",
                False,
            )
        case "trigger_dead_lettered":
            tries = _number(r"failed (\d+) delivery", text)
            if trigger is not None:
                rule = trigger.rule_id.replace("_", " ")
                where = f"the {minute_label(trigger.period, trigger.clock_ms)} {rule}"
            else:
                where = "a match moment"
            after = f" after {tries} tries" if tries else ""
            return Brief(f"No insight could be made for {where}{after}, so it was dropped.",
                         TELL_ENGINEER, True)  # fmt: skip
        case "feed_gap":
            secs = _number(r"for ([\d.]+) s", text)
            at = _number(r"last at ([^)]+)\)", text)
            span = f" for {secs} seconds" if secs else ""
            return Brief(f"The live match data stopped arriving{span}"
                         f"{f' at {at}' if at else ''}.",
                         f"Insights wait until it is back. {TELL_ENGINEER}", True)  # fmt: skip
        case "feed_lag":
            p95 = _number(r"p95 ([\d.]+) s", text)
            behind = f" about {p95} seconds" if p95 else ""
            return Brief(f"The live match data is arriving{behind} late.",
                         f"Graphics reach viewers late. {TELL_ENGINEER}", True)  # fmt: skip
        case "validation_reject":
            n = int(_number(r"^(\d+) feed row", text) or 0)
            rows = _plural(n, "faulty data row") if n else "Faulty data rows"
            return Brief(f"{rows[0].upper()}{rows[1:]} from the live feed "
                         f"{'was' if n == 1 else 'were'} dropped before analysis.",
                         NOTHING_TO_DO, False,
                         ["Insights only use rows that passed every check."])  # fmt: skip
        case "validation_flag":
            n = int(_number(r"^(\d+) feed row", text) or 0)
            rows = _plural(n, "data row") if n else "Some data rows"
            return Brief(f"{rows[0].upper()}{rows[1:]} from the live feed looked unusual but "
                         f"{'was' if n == 1 else 'were'} kept.", NOTHING_TO_DO, False)  # fmt: skip
    return Brief(text, NOTHING_TO_DO, alert.severity == "critical")


def explain_step(record: AuditRecord) -> str:
    """One audit-trail step as a plain sentence."""
    who = {"explainer": "The Explainer", "narrator": "The Narrator"}.get(
        record.actor, record.actor.replace("_", " ").capitalize()
    )
    why = reasons(record.findings)
    because = f": {join(why)}" if why else ""
    match record.action:
        case "draft":
            return f"{who} wrote a draft."
        case "fallback" if record.actor == "copy_check":
            per_mode = versions(record.findings)
            n = sum(len(langs) for langs in per_mode.values()) or len(record.findings)
            return (f"{_plural(n, 'on-screen version')} used pre-written wording "
                    f"(the AI's text had {join(why)}).")  # fmt: skip
        case "fallback":
            return f"Pre-written wording was used instead of the AI's text{because}."
        case "inject_error":
            return "Demo test: a wrong number was planted on purpose for the Auditor to catch."
        case "check":
            return ("The Auditor checked every number against the match data: all correct."
                    if record.passed else f"The Auditor found a problem{because}.")  # fmt: skip
        case "review":
            if record.passed:
                return "The Auditor reviewed the claims and tone: fine."
            return f"The Auditor's review flagged the wording{because}."
        case "revise":
            return "Sent back to be fixed."
        case "approve":
            return "The Auditor passed it first time."
        case "correct":
            return "The Auditor passed it after a fix."
        case "block":
            return "The Auditor blocked it. It will not air."
        case "producer_approve":
            return "A producer approved it for air."
        case "producer_block":
            return "A producer blocked it."
    return f"{who}: {record.action.replace('_', ' ')}."
