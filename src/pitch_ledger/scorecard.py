"""Evaluation scorecard: the numbers behind "every insight is checked before it airs".

Sections:
- detection: each demo match's planted scenario found in time (latency in match minutes),
  false momentum alerts, and a seed sweep of every planted scenario and baseline;
- accuracy: every aired insight recomputed independently from the raw rows (its metrics,
  every number in its text and in each of its overlays, its evidence);
- planted errors: a wrong number planted in the first draft of every full-flow insight;
  caught means the Auditor rejected that draft, and no wrong number aired;
- coverage: audience modes x languages per aired insight, template fallbacks;
- outcomes, processing time and model usage.

The demo matches are generated fresh into a temporary folder, so the scorecard does not
depend on local data. Offline by default (zero cost).
"""

import asyncio
import statistics
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pitch_ledger.agents import copy
from pitch_ledger.agents.editorial import draft_issues, editorial_issues
from pitch_ledger.agents.facts import build_facts
from pitch_ledger.agents.replay import ReplayOptions, ReplayRun, replay_match
from pitch_ledger.engine.detector import detect
from pitch_ledger.engine.evaluation import check_detection
from pitch_ledger.engine.recompute import NotRecomputable, recompute_trigger
from pitch_ledger.engine.season import SeasonView, has_season_facts
from pitch_ledger.engine.windows import match_minute
from pitch_ledger.history import InMemoryHistory, build_record, season_for
from pitch_ledger.ledger import InMemoryLedger
from pitch_ledger.matchlab.build import build_match
from pitch_ledger.matchlab.generator import (
    DEFAULT_KICKOFF,
    MatchSpec,
    SimulatedMatch,
    simulate_match,
)
from pitch_ledger.matchlab.io import write_match
from pitch_ledger.matchlab.scenarios import ManifestMatch, load_manifest, load_scenario, plan_for
from pitch_ledger.schema import Insight, TrackingSample
from pitch_ledger.schema.enums import AuditStatus, ScenarioId
from pitch_ledger.validation.insight_rules import unsupported_numbers

COMBINATIONS = len(copy.MODES) * len(copy.LANGUAGES)


def _spec(entry: ManifestMatch, seed: int | None = None) -> MatchSpec:
    return MatchSpec(
        match_id=entry.match_id,
        home_team_id=entry.home_team_id,
        away_team_id=entry.away_team_id,
        seed=entry.seed if seed is None else seed,
        scenario_id=entry.scenario_id.value,
        kickoff_utc=entry.kickoff or DEFAULT_KICKOFF,
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


@dataclass
class _Match:
    entry: ManifestMatch
    sim: SimulatedMatch
    tracking: list[TrackingSample]
    clean: ReplayRun
    planted: ReplayRun
    season: SeasonView | None = None


# Sections ---------------------------------------------------------------------------------


def detection_section(matches: Sequence[_Match], sweep_seeds: int) -> dict[str, Any]:
    demo: list[dict[str, Any]] = []
    for m in matches:
        check = check_detection(load_scenario(m.entry.scenario_id.value), m.clean.triggers)
        demo.append(
            {
                "match_id": m.entry.match_id,
                "scenario": m.entry.scenario_id.value,
                "expected": check.scenario.expected_detection is not None,
                "detected": check.hit.trigger_id if check.hit else None,
                "latency_min": check.latency_min,
                "false_momentum": [t.trigger_id for t in check.false_momentum],
            }
        )
    expected = [d for d in demo if d["expected"]]
    latencies = [d["latency_min"] for d in expected if d["latency_min"] is not None]
    sweep: dict[str, Any] = {}
    for entry in (m.entry for m in matches):
        scenario = load_scenario(entry.scenario_id.value)
        plan = plan_for(scenario, entry.home_team_id, entry.away_team_id)
        hits = alerts = 0
        for seed in range(1, sweep_seeds + 1):
            sim = simulate_match(_spec(entry, seed), plan.modifiers, plan.send_offs, plan.protected)
            check = check_detection(scenario, detect(sim.match, sim.events))
            hits += check.hit is not None
            alerts += bool(check.false_momentum)
        planted = entry.scenario_id is not ScenarioId.NONE
        sweep[entry.match_id] = {
            "scenario": entry.scenario_id.value,
            "seeds": sweep_seeds,
            "detected": hits if planted else None,
            "false_alert_matches": alerts,
        }
    planted_runs = [s for s in sweep.values() if s["detected"] is not None]
    return {
        "demo": demo,
        "detection_rate": _rate(sum(d["detected"] is not None for d in expected), len(expected)),
        "latency_min": {
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
            "max": max(latencies, default=None),
        },
        "false_alerts": sum(len(d["false_momentum"]) for d in demo),
        "sweep": sweep,
        "sweep_detection_rate": _rate(
            sum(s["detected"] for s in planted_runs), sum(s["seeds"] for s in planted_runs)
        ),
        "sweep_false_alert_rate": _rate(
            sum(s["false_alert_matches"] for s in sweep.values()),
            sum(s["seeds"] for s in sweep.values()),
        ),
    }


def insight_checks(
    ledger: InMemoryLedger, sim: SimulatedMatch, tracking: Sequence[TrackingSample],
    insight: Insight, season: SeasonView | None = None,
) -> dict[str, list[str]]:  # fmt: skip
    """Independent code checks on one aired insight, by name; an empty list passed.

    - recompute: every metric recomputes from the raw rows to the stated value;
    - numbers: every number in the headline and explanation is one of its metrics;
    - evidence: cited events exist and belong to the trigger;
    - overlays: every number in every overlay recomputes (in its language);
    - editorial: the headline and explanation pass the house-style check.

    ``season``: the earlier matches' records, for season facts."""
    trigger = ledger.get_trigger(insight.trigger_id or "")
    if trigger is None:
        return {"recompute": ["no trigger"]}
    try:
        fresh = recompute_trigger(trigger, sim.match, sim.events, list(tracking), season=season)
    except NotRecomputable as exc:
        return {"recompute": [str(exc)]}
    text = f"{insight.headline}\n{insight.explanation}"
    event_ids = {e.event_id for e in sim.events}
    in_moment = set(insight.evidence_event_ids) <= set(trigger.evidence_event_ids) & event_ids
    return {
        "recompute": [f"{k}={v} (recomputed {fresh.get(k)})"
                      for k, v in insight.metrics.items() if fresh.get(k) != v],
        "numbers": [f"text: {n}" for n in unsupported_numbers(text, insight.metrics.values())],
        "evidence": [] if in_moment else ["evidence outside the trigger"],
        "overlays": [f"overlay {o.overlay_id} ({o.language}): {n}"
                     for o in ledger.overlays(insight.insight_id)
                     for n in unsupported_numbers(o.text, fresh.values(), o.language)],
        "editorial": draft_issues(insight.headline, insight.explanation,
                                  season=has_season_facts(trigger.metrics)),
    }  # fmt: skip


def _insight_problems(m: _Match, run: ReplayRun, insight: Insight) -> list[str]:
    """What an independent recompute finds wrong with an aired insight (empty: accurate)."""
    checks = insight_checks(run.ledger, m.sim, m.tracking, insight, m.season)
    return [p for name in ("recompute", "numbers", "evidence", "overlays")
            for p in checks.get(name, [])]  # fmt: skip


CHECKS = ("recompute", "numbers", "evidence", "overlays", "editorial")
"""The code checks in an evaluation row; each is "pass" or "fail"."""


def demo_match(match_id: str) -> tuple[SimulatedMatch, list[TrackingSample]]:
    """A demo match rebuilt from its manifest seed (the same rows the ledger was made from)."""
    entry = next((e for e in load_manifest() if e.match_id == match_id), None)
    if entry is None:
        raise KeyError(f"{match_id} is not a demo match (scenarios/matches.yaml)")
    return build_match(_spec(entry))


def evaluation_rows(
    ledger: InMemoryLedger,
    sim: SimulatedMatch,
    tracking: Sequence[TrackingSample],
    season: SeasonView | None = None,
) -> list[dict[str, str]]:
    """One row per aired insight, for an evaluation service: the moment (query), what the
    agents were given (context: the code-computed metrics, names and signals, and the cited
    evidence), what aired (response), and each code check as "pass" or "fail"."""
    teams = {t.team_id: t for t in sim.teams}
    events = {e.event_id: e for e in sim.events}
    rows = []
    for insight in ledger.insights(sim.match.match_id):
        if insight.approved_by is None or insight.audit_status is AuditStatus.BLOCKED:
            continue
        trigger = ledger.get_trigger(insight.trigger_id or "")
        if trigger is None:
            continue
        facts = build_facts(trigger, list(teams.values()), sim.players,
                            sim.match.home_team_id, sim.match.away_team_id, events)  # fmt: skip
        names = {k: v for k, v in facts.names().items() if v}
        minute = f"{round(match_minute(trigger.period, trigger.clock_ms)):.0f}'"
        who = (names.get("player") if trigger.subject.player_id else None) or names.get(
            "team", "the match"
        )
        checks = insight_checks(ledger, sim, tracking, insight, season)
        context = [
            "Metrics computed in code (exact): "
            + ", ".join(f"{k}={v}" for k, v in insight.metrics.items()),
            "Names: " + ", ".join(f"{k}={v}" for k, v in names.items()),
            *(f"Context: {signal}" for signal in facts.signals),
            "Evidence events: " + (", ".join(insight.evidence_event_ids) or "none"),
        ]
        rows.append({
            "insight_id": insight.insight_id,
            "match_id": insight.match_id,
            "insight_type": insight.insight_type.value,
            "query": f"Explain this {trigger.rule_id.replace('_', ' ')} moment at {minute} "
                     f"({who}) for a studio audience, using only the given metrics.",
            "context": "\n".join(context),
            "response": f"{insight.headline}\n{insight.explanation}",
            **{name: "fail" if checks.get(name) else "pass" for name in CHECKS},
            "problems": "; ".join(p for found in checks.values() for p in found)[:500],
        })  # fmt: skip
    return rows


def accuracy_section(matches: Sequence[_Match]) -> dict[str, Any]:
    aired = problems = overlays = 0
    examples: list[str] = []
    for m in matches:
        for insight in m.clean.ledger.insights():
            if insight.approved_by is None or insight.audit_status is AuditStatus.BLOCKED:
                continue
            aired += 1
            overlays += len(m.clean.ledger.overlays(insight.insight_id))
            found = _insight_problems(m, m.clean, insight)
            problems += bool(found)
            examples += [f"{insight.insight_id}: {p}" for p in found[:3]]
    return {
        "aired_insights": aired,
        "overlays_checked": overlays,
        "accurate_insights": aired - problems,
        "factual_accuracy": _rate(aired - problems, aired),
        "problems": examples[:20],
    }


def planted_section(matches: Sequence[_Match]) -> dict[str, Any]:
    planted = caught = aired_wrong = 0
    by_type: Counter[str] = Counter()
    final: Counter[str] = Counter()
    missed: list[str] = []
    for m in matches:
        ledger = m.planted.ledger
        for record in (a for a in ledger.audits() if a.action == "inject_error"):
            planted += 1
            trail = ledger.audits(record.insight_id)
            rejected = any(
                a.action in ("revise", "block") and a.round == record.round for a in trail
            )
            insight = ledger.get_insight(record.insight_id)
            if insight is None:
                continue
            final[insight.audit_status.value] += 1
            by_type[insight.insight_type.value] += 1
            wrong = insight.audit_status is not AuditStatus.BLOCKED and bool(
                _insight_problems(m, m.planted, insight)
            )
            caught += rejected and not wrong
            aired_wrong += wrong
            if not rejected or wrong:
                missed.append(f"{record.insight_id}: {record.findings[0]}")
    return {
        "planted": planted,
        "caught": caught,
        "catch_rate": _rate(caught, planted),
        "wrong_numbers_passed": aired_wrong,
        "final_status": dict(final),
        "by_insight_type": dict(by_type),
        "missed": missed,
    }


def coverage_section(matches: Sequence[_Match]) -> dict[str, Any]:
    aired = complete = fallbacks = 0
    languages: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    for m in matches:
        ledger = m.clean.ledger
        for insight in ledger.insights():
            overlays = ledger.overlays(insight.insight_id)
            if not overlays:
                continue
            aired += 1
            combos = {(o.audience_mode.value, o.language) for o in overlays}
            complete += len(combos) == COMBINATIONS
            languages.update(o.language for o in overlays)
            modes.update(o.audience_mode.value for o in overlays)
        fallbacks += sum(len(a.findings) for a in ledger.audits() if a.action == "fallback")
    return {
        "insights_with_overlays": aired,
        "complete_mode_language_sets": complete,
        "coverage": _rate(complete, aired),
        "overlays_by_language": dict(languages),
        "overlays_by_mode": dict(modes),
        "template_fallbacks": fallbacks,
    }


def editorial_section(matches: Sequence[_Match]) -> dict[str, Any]:
    """Form of the aired copy (length, clichés, unsupported claims, repetition), whether the
    five modes say it differently, and how many repeat alerts the detector folded away."""
    issues: list[str] = []
    texts = sets = indistinct = superseded = 0
    for m in matches:
        ledger = m.clean.ledger
        superseded += sum(bool(t.context.get("superseded_by")) for t in m.clean.triggers)
        for insight in ledger.insights():
            overlays = ledger.overlays(insight.insight_id)
            if not overlays:
                continue
            trigger = ledger.get_trigger(insight.trigger_id or "")
            season = trigger is not None and has_season_facts(trigger.metrics)
            found = draft_issues(insight.headline, insight.explanation, season=season)
            issues += [f"{insight.insight_id}: {f}" for f in found]
            by_language: dict[str, list[str]] = {}
            for o in overlays:
                texts += 1
                problems = editorial_issues(o.text, "overlay", o.language, season=season)
                issues += [f"{o.overlay_id} ({o.language}): {f}" for f in problems]
                by_language.setdefault(o.language, []).append(o.text)
            for group in by_language.values():
                sets += 1
                indistinct += len(set(group)) < len(group)
    return {
        "texts_checked": texts,
        "issues": len(issues),
        "examples": issues[:20],
        "mode_sets": sets,
        "mode_sets_with_repeats": indistinct,
        "superseded_triggers": superseded,
    }


def outcomes_section(matches: Sequence[_Match]) -> dict[str, Any]:
    status: Counter[str] = Counter()
    routes: Counter[str] = Counter()
    seconds: list[float] = []
    dead = 0
    for m in matches:
        report = m.clean.report
        routes.update(report.counts()["routes"])
        status.update(i.audit_status.value for i in m.clean.ledger.insights())
        seconds += [report.seconds[o.trigger_id] for o in report.outcomes if o.status == "aired"]
        dead += len(report.dead_letters)
    audited = status["approved"] + status["corrected"] + status["blocked"]
    ordered = sorted(seconds)
    return {
        "routes": dict(routes),
        "audit_status": dict(status),
        "first_pass_approval": _rate(status["approved"], audited),
        "blocked": status["blocked"],
        "dead_letters": dead,
        "processing_s": {
            "p50": round(statistics.median(ordered), 3) if ordered else None,
            "p95": round(ordered[int(0.95 * (len(ordered) - 1))], 3) if ordered else None,
            "max": round(ordered[-1], 3) if ordered else None,
        },
    }


def usage_section(matches: Sequence[_Match]) -> dict[str, Any]:
    total: Counter[str] = Counter()
    by_role: Counter[str] = Counter()
    for m in matches:
        for run in (m.clean, m.planted):
            usage = run.summary["usage"]
            total.update(
                {k: usage[k] for k in ("calls", "failures", "input_tokens", "output_tokens")}
            )
            by_role.update(usage["by_role"])
    first = matches[0].clean.summary if matches else {}
    return {
        "provider": first.get("provider"),
        "model_version": first.get("model_version"),
        **dict(total),
        "by_role": dict(by_role),
    }


# Scorecard --------------------------------------------------------------------------------

Gate = tuple[str, bool]


def gates(card: dict[str, Any]) -> list[Gate]:
    """Pass/fail lines for the scorecard (``evaluate`` exits 1 if any fails)."""
    d, a, p, c, o = (card[k] for k in ("detection", "accuracy", "planted", "coverage", "outcomes"))
    return [
        ("planted scenarios detected in time", d["detection_rate"] in (1.0, None)),
        ("no false momentum alerts on the demo matches", d["false_alerts"] == 0),
        ("every aired insight recomputes exactly", a["factual_accuracy"] in (1.0, None)),
        ("every planted error caught", p["catch_rate"] in (1.0, None)),
        ("no wrong number passed the Auditor", p["wrong_numbers_passed"] == 0),
        ("every aired insight in 5 modes x 3 languages", c["coverage"] in (1.0, None)),
        ("aired copy passes the editorial check", card["editorial"]["issues"] == 0),
        ("no dead letters", o["dead_letters"] == 0),
    ]


async def _run_match(
    entry: ManifestMatch,
    built: tuple[SimulatedMatch, list[TrackingSample]],
    root: Path,
    provider: Literal["offline", "foundry"] | None,
    history: InMemoryHistory,
) -> _Match:
    sim, tracking = built
    match_dir = root / entry.match_id
    write_match(match_dir, sim.match, sim.teams, sim.players, sim.events, tracking)
    common: dict[str, Any] = {"stats": "local", "write": False, "provider": provider}
    clean = await replay_match(match_dir, ReplayOptions(approve=True, **common), history=history)
    planted = await replay_match(match_dir, ReplayOptions(inject="all", **common), history=history)
    return _Match(entry, sim, tracking, clean, planted, season_for(sim.match, history))


def build_scorecard(
    matches: Sequence[ManifestMatch] | None = None,
    sweep_seeds: int = 5,
    provider: Literal["offline", "foundry"] | None = None,
) -> dict[str, Any]:
    entries = list(matches) if matches is not None else load_manifest()
    built = {e.match_id: build_match(_spec(e)) for e in entries}
    # The season: every match's record, so each match sees the ones that kicked off before.
    history = InMemoryHistory([build_record(sim.match, sim.events, tracking)
                               for sim, tracking in built.values()])  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        runs = [asyncio.run(_run_match(e, built[e.match_id], Path(tmp), provider, history))
                for e in entries]  # fmt: skip
    card: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "matches": [e.match_id for e in entries],
        "detection": detection_section(runs, sweep_seeds),
        "accuracy": accuracy_section(runs),
        "planted": planted_section(runs),
        "coverage": coverage_section(runs),
        "editorial": editorial_section(runs),
        "outcomes": outcomes_section(runs),
        "usage": usage_section(runs),
    }
    card["gates"] = {name: ok for name, ok in gates(card)}
    card["passed"] = all(card["gates"].values())
    return card
