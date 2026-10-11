"""``pitch-ledger`` command line: generate, validate, detect, replay, evaluate and the live feed."""

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pitch_ledger.engine import match_minute
from pitch_ledger.engine.detector import detect
from pitch_ledger.engine.evaluation import check_detection
from pitch_ledger.matchlab.build import build_match
from pitch_ledger.matchlab.generator import DEFAULT_KICKOFF, MatchSpec
from pitch_ledger.matchlab.io import MATCH_FILE, read_match, write_match, write_triggers
from pitch_ledger.matchlab.scenarios import load_manifest, load_scenario
from pitch_ledger.schema import Event, TrackingSample, Trigger
from pitch_ledger.schema.enums import ScenarioId
from pitch_ledger.validation import RULES, measure, validate_match
from pitch_ledger.validation.realism import RealismCheck

if TYPE_CHECKING:
    from pitch_ledger.history import HistoryStore
    from pitch_ledger.infra_adapters.feeds import FeedSettings
    from pitch_ledger.infra_adapters.ledger_queue import LedgerQueueSettings
    from pitch_ledger.infra_adapters.stores import StoreSettings
    from pitch_ledger.ledger import InMemoryLedger

IMPORTANT = {"momentum_shift", "key_moment", "player_form", "recap"}
"""Insight types ``detect`` lists by default (``--all`` lists every trigger)."""


def _match_id_for(out: Path, given: str | None) -> str:
    if given:
        return given
    return out.name if re.fullmatch(r"M\d{4}", out.name) else "M0001"


def _write(spec: MatchSpec, out: Path) -> None:
    sim, tracking = build_match(spec)
    paths = write_match(out, sim.match, sim.teams, sim.players, sim.events, tracking)
    last = sim.events[-1]
    print(
        f"{spec.match_id} ({spec.scenario_id}, seed {spec.seed}): {sim.match.home_team_id} "
        f"{last.score_home}-{last.score_away} {sim.match.away_team_id}, "
        f"{len(sim.events)} events, {len(tracking)} tracking rows -> {out}"
    )
    for path in paths:
        print(f"  wrote {path}")


def _start_tracing(service: str) -> None:
    """Send agent and insight spans to Application Insights when PITCH_LEDGER_TRACING=azure."""
    from pitch_ledger.infra_adapters.telemetry import TelemetrySettings, configure_tracing

    settings = TelemetrySettings()
    if configure_tracing(service, settings):
        print(f"tracing: {settings.describe()}")


def cmd_generate(args: argparse.Namespace) -> int:
    known = [s.value for s in ScenarioId]
    if args.scenario not in known:
        print(f"unknown scenario {args.scenario!r}; known: {', '.join(known)}", file=sys.stderr)
        return 2
    out = Path(args.out)
    spec = MatchSpec(
        match_id=_match_id_for(out, args.match_id),
        home_team_id=args.home,
        away_team_id=args.away,
        seed=args.seed,
        kickoff_utc=datetime.fromisoformat(args.kickoff) if args.kickoff else DEFAULT_KICKOFF,
        scenario_id=args.scenario,
    )
    try:
        _write(spec, out)
    except ValueError as exc:
        print(f"cannot generate: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_generate_all(args: argparse.Namespace) -> int:
    root = Path(args.out)
    for entry in load_manifest():
        spec = MatchSpec(
            match_id=entry.match_id,
            home_team_id=entry.home_team_id,
            away_team_id=entry.away_team_id,
            seed=entry.seed,
            scenario_id=entry.scenario_id.value,
            kickoff_utc=entry.kickoff or DEFAULT_KICKOFF,
        )
        _write(spec, root / entry.match_id)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    files = read_match(Path(args.match_dir), with_tracking=not args.no_tracking)
    report = validate_match(
        files.match,
        files.players,
        files.events,
        None if args.no_tracking else files.tracking,
        realism=not args.no_realism,
    )
    print(
        f"{files.match.match_id}: {report.events_checked} events, "
        f"{report.tracking_checked} tracking rows checked"
    )
    counts = report.counts()
    for rule_id, rule in RULES.items():
        if rule.level == "insight":
            continue
        n = counts.get(rule_id, 0)
        status = "ok" if n == 0 else f"{n} {rule.action}"
        print(f"  {rule_id:<9} {status:<12} {rule.description}")
    for result in report.results[: args.show]:
        print(f"  ! {result.rule_id} {result.action} {result.event_id or '-'}: {result.message}")
    if len(report.results) > args.show:
        print(f"  ... {len(report.results) - args.show} more")
    print("PASS" if report.ok else "FAIL (rejected rows)")
    return 0 if report.ok else 1


def format_realism(checks: Sequence[RealismCheck]) -> str:
    """A plain-text realism table: metric, measured value, band, target, status."""
    header = f"{'metric':<22} {'value':<42} {'band':<12} {'target':<16} status"
    lines = [header, "-" * len(header)]
    for c in checks:
        band = f"{c.band.low:g}-{c.band.high:g}"
        lines.append(
            f"{c.band.metric:<22} {c.summary():<42} {band:<12} {c.band.target:<16} "
            f"{'in band' if c.ok else 'OUT'}"
        )
    return "\n".join(lines)


def cmd_realism(args: argparse.Namespace) -> int:
    files = read_match(Path(args.match_dir))
    events = [Event.model_validate(e) for e in files.events]
    tracking = [TrackingSample.model_validate(s) for s in files.tracking]
    checks = measure(files.match, files.players, events, tracking)
    print(
        f"{files.match.match_id} realism (scenario {files.match.scenario_id}, "
        f"seed {files.match.random_seed})"
    )
    print(format_realism(checks))
    out = [c.band.metric for c in checks if not c.ok]
    print("all metrics in band" if not out else f"out of band: {', '.join(out)}")
    return 1 if out and args.strict else 0


def _brief(trigger: Trigger) -> str:
    shown = list(trigger.metrics.items())[:4]
    return ", ".join(f"{k} {v:g}" for k, v in shown)


def cmd_detect(args: argparse.Namespace) -> int:
    match_dir = Path(args.match_dir)
    files = read_match(match_dir, with_tracking=not args.no_tracking)
    events = [Event.model_validate(e) for e in files.events]
    tracking = [TrackingSample.model_validate(s) for s in files.tracking]
    triggers = detect(files.match, events, tracking)
    path = write_triggers(match_dir, triggers)
    print(f"{files.match.match_id}: {len(triggers)} triggers -> {path}")
    for t in triggers:
        if args.all or t.insight_type.value in IMPORTANT:
            who = t.subject.player_id or t.subject.team_id or "match"
            minute = match_minute(t.period, t.clock_ms)
            print(f"  {t.trigger_id} {minute:5.1f}' {t.rule_id:<18} {who:<8} {_brief(t)}")
    scenario = load_scenario(files.match.scenario_id)
    check = check_detection(scenario, triggers)
    expected = scenario.expected_detection
    if expected is not None:
        target = expected.player_id or expected.team_id
        if check.hit is not None:
            print(
                f"expected {expected.insight_type.value} for {target} within "
                f"{expected.within_min} min: found {check.hit.trigger_id} after "
                f"{check.latency_min} min"
            )
        else:
            print(
                f"expected {expected.insight_type.value} for {target} within "
                f"{expected.within_min} min: NOT FOUND"
            )
    if check.false_momentum:
        ids = ", ".join(t.trigger_id for t in check.false_momentum)
        print(f"false momentum alerts: {ids}")
    elif scenario.expected_detection is None:
        print("no false momentum alerts")
    return 0 if check.passed else 1


def cmd_mcp(args: argparse.Namespace) -> int:
    from pitch_ledger.mcp_server.server import serve

    serve(args.match_dir)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    import asyncio

    from pitch_ledger.agents.replay import ReplayOptions, replay_match

    _start_tracing("pitch-ledger-replay")
    only = tuple(x.strip() for x in args.only.split(",") if x.strip()) if args.only else None
    options = ReplayOptions(
        approve=args.approve,
        inject="first" if args.inject_error else "none",
        provider=args.provider,
        stats="local" if args.local_stats else "mcp",
        only=only,
        budget=args.budget,
    )
    from pitch_ledger.infra_adapters.ledger_queue import (
        LedgerQueueSettings,
        describe_history,
        open_history,
        open_ledger,
        open_queue,
    )

    overrides = {k: v for k, v in (("ledger", args.ledger), ("queue", args.queue),
                                   ("history", args.history)) if v and v != "none"}  # fmt: skip
    services = LedgerQueueSettings(**overrides)
    match_dir = Path(args.match_dir)
    match_id = read_match(match_dir, with_tracking=False).match.match_id
    try:
        cloud_ledger = open_ledger(services, fresh_match=match_id)
        queue = open_queue(services)
        history = None if args.history == "none" else open_history(services, match_dir.parent)
    except ValueError as exc:
        print(f"ledger/queue/history settings: {exc}", file=sys.stderr)
        return 2
    if services.ledger != "file" or services.queue != "memory":
        print(f"ledger: {services.describe_ledger()}; queue: {services.describe_queue()}")
    try:
        run = asyncio.run(replay_match(match_dir, options, cloud_ledger, queue, history))
    finally:
        for part in (cloud_ledger, queue, history):
            close = getattr(part, "close", None)
            if close is not None:
                close()
    summary, ledger = run.summary, run.ledger
    if history is not None:
        earlier = ", ".join(summary["season_matches"] or []) or "none"
        print(f"season: earlier matches {earlier} (history {describe_history(services)}); "
              f"{run.match_id}'s record kept")  # fmt: skip
    counts = run.report.counts()
    routes, status = counts["routes"], counts["status"]
    print(
        f"{run.match_id} replay: {summary['triggers']} triggers (full flow "
        f"{routes.get('full', 0)}, auto {routes.get('auto', 0)}, stored "
        f"{routes.get('stored', 0)}); models {summary['model_version']}; stats via "
        f"{'MCP' if summary['stats'] == 'mcp' else 'in-process tools'}"
    )
    for outcome in run.report.outcomes:
        insight = ledger.get_insight(outcome.insight_id or "")
        if insight is None:
            continue
        minute = match_minute(insight.period, insight.clock_ms)
        trigger = ledger.get_trigger(outcome.trigger_id)
        rule = trigger.rule_id if trigger else ""
        print(
            f"  {insight.insight_id} {minute:5.1f}' {rule:<18} {outcome.audit_status:<9} "
            f"{outcome.status:<7} {outcome.overlays:>3} overlays  {insight.headline[:60]}"
        )
    for planted in (a for a in ledger.audits() if a.action == "inject_error"):
        trail = ledger.audits(planted.insight_id)
        caught = any(a.action in ("revise", "block") and a.round == planted.round for a in trail)
        final = ledger.get_insight(planted.insight_id)
        print(
            f"planted error in {planted.insight_id}: {planted.findings[0]} -> "
            f"{'caught by the Auditor' if caught else 'NOT CAUGHT'}; insight "
            f"{final.audit_status.value if final else 'not stored'}"
        )
    print(
        f"insights: aired {status.get('aired', 0)}, pending {status.get('pending', 0)}, "
        f"blocked {status.get('blocked', 0)}; overlays {summary['overlays']}"
    )
    if status.get("pending"):
        print("  pending insights wait for a producer (--approve approves them as producer_demo)")
    usage = summary["usage"]
    print(
        f"model calls {usage['calls']} ({usage['failures']} failed; tokens in "
        f"{usage['input_tokens']}, out {usage['output_tokens']}); stats calls "
        f"{summary['stats_calls']}; {summary['duration_s']} s"
    )
    for dead in summary["dead_letters"]:
        print(
            f"DEAD LETTER {dead['trigger_id']} after {dead['deliveries']} deliveries: "
            f"{dead['errors'][-1]}"
        )
    if run.ledger_dir is not None:
        print(f"ledger -> {run.ledger_dir}")
    return 1 if summary["dead_letters"] else 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    import json

    from pitch_ledger.scorecard import build_scorecard

    entries = load_manifest()
    if args.match:
        entries = [e for e in entries if e.match_id in set(args.match)]
        if not entries:
            print(f"no demo match among {', '.join(args.match)}", file=sys.stderr)
            return 2
    card = build_scorecard(entries, args.sweep, args.provider)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(card, indent=2) + "\n", "utf-8")
    if args.store:  # keep the run in the evaluation history (EvalRuns)
        import time

        from pitch_ledger.eventstore.rows import eval_run_row
        from pitch_ledger.infra_adapters.stores import open_store

        settings = _store_settings(args.store)
        store = open_store(settings)
        try:
            store.append("EvalRuns", [eval_run_row(card, "scorecard", time.time())])
            store.flush()
        finally:
            store.close()
        print(f"run kept in EvalRuns ({settings.describe()})")
    d, a, p, c, o, u = (
        card[k] for k in ("detection", "accuracy", "planted", "coverage", "outcomes", "usage")
    )
    print(f"scorecard for {', '.join(card['matches'])} ({u['model_version']})")
    print(
        f"  detection: {d['detection_rate']} of planted scenarios in time (mean latency "
        f"{d['latency_min']['mean']} min); false alerts {d['false_alerts']}; seed sweep "
        f"{d['sweep_detection_rate']} detected, {d['sweep_false_alert_rate']} with false alerts"
    )
    print(
        f"  accuracy: {a['accurate_insights']}/{a['aired_insights']} aired insights recompute "
        f"exactly ({a['overlays_checked']} overlays checked)"
    )
    print(
        f"  planted errors: {p['caught']}/{p['planted']} caught; wrong numbers passed "
        f"{p['wrong_numbers_passed']}; final {p['final_status']}"
    )
    print(
        f"  coverage: {c['complete_mode_language_sets']}/{c['insights_with_overlays']} insights "
        f"in every mode and language; template fallbacks {c['template_fallbacks']}"
    )
    e = card["editorial"]
    print(
        f"  editorial: {e['issues']} issues in {e['texts_checked']} on-screen texts; "
        f"{e['mode_sets_with_repeats']}/{e['mode_sets']} mode sets repeat a text; "
        f"{e['superseded_triggers']} repeat alerts folded"
    )
    print(
        f"  outcomes: {o['audit_status']}; first-pass approval {o['first_pass_approval']}; "
        f"processing p50 {o['processing_s']['p50']} s"
    )
    print(
        f"  model calls {u.get('calls', 0)} ({u.get('failures', 0)} failed; tokens in "
        f"{u.get('input_tokens', 0)}, out {u.get('output_tokens', 0)})"
    )
    for name, ok in card["gates"].items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"scorecard -> {out}")
    return 0 if card["passed"] else 1


def cmd_foundry_eval(args: argparse.Namespace) -> int:
    import json

    from pitch_ledger.history import season_for
    from pitch_ledger.infra_adapters.ledger_queue import open_history
    from pitch_ledger.ledger import FileLedger, InMemoryLedger
    from pitch_ledger.matchlab.scenarios import load_manifest
    from pitch_ledger.scorecard import CHECKS, demo_match, evaluation_rows

    matches = args.match or [e.match_id for e in load_manifest()]
    ledger_kind = args.ledger or _ledger_settings().ledger
    # Season facts recompute from the records the replays used: by default the store that
    # sits with the ledger (Cosmos with a Cosmos ledger).
    stores = _ledger_settings().model_copy(update={"history": args.history or ledger_kind})
    history = open_history(stores, Path(args.data))
    rows: list[dict[str, str]] = []
    for match_id in matches:
        ledger: InMemoryLedger
        if ledger_kind == "cosmos":
            ledger = _cosmos_reader(match_id)
        else:
            ledger = FileLedger(Path(args.data) / match_id / "ledger")
        sim, tracking = demo_match(match_id)
        found = evaluation_rows(ledger, sim, tracking, season_for(sim.match, history))
        print(f"{match_id}: {len(found)} aired insights ({ledger_kind} ledger)")
        rows += found
    failed = {name: sum(r[name] != "pass" for r in rows) for name in CHECKS}
    print(f"code checks over {len(rows)} insights: "
          + ", ".join(f"{k} {len(rows) - v}/{len(rows)}" for k, v in failed.items()))  # fmt: skip
    out = Path(args.data) / "foundry_eval_rows.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    print(f"rows -> {out}")
    if args.dry_run:
        return 0 if not any(failed.values()) else 1

    from pitch_ledger.agents.model_manager import ModelSettings
    from pitch_ledger.infra_adapters.foundry_evals import (
        JUDGES,
        SAFETY,
        open_client,
        publish,
        scorecard_metadata,
    )

    settings = ModelSettings()
    deployment = settings.deployment("strong")
    if not settings.foundry_project_endpoint or not deployment:
        print("foundry-eval needs FOUNDRY_PROJECT_ENDPOINT and PITCH_LEDGER_MODEL_STRONG")
        return 1
    card_path = Path(args.scorecard)
    card = json.loads(card_path.read_text()) if card_path.exists() else None
    judges = not args.no_judges
    calls = len(rows) * ((len(JUDGES) if judges else 0) + (len(SAFETY) if args.safety else 0))
    gates = "" if card else " (no scorecard file: gates not attached)"
    print(f"publishing to Foundry: {len(rows)} rows, about {calls} evaluator calls{gates}")
    result = publish(
        rows, open_client(settings.foundry_project_endpoint), deployment,
        judges=judges, safety=args.safety, run_name=args.name,
        metadata=scorecard_metadata(card, f"{ledger_kind} ledger: {', '.join(matches)}"),
    )  # fmt: skip
    print(f"run {result.run_id}: {result.status}" + (f" ({result.error})" if result.error else ""))
    for name, c in result.criteria.items():
        score = f", mean score {c['mean_score']}" if c.get("mean_score") is not None else ""
        print(f"  {name}: {c['passed']} passed, {c['failed']} failed{score}")
    if result.report_url:
        print(f"report: {result.report_url}")
    return 0 if result.status == "completed" and result.code_checks_passed() else 1


def _ledger_settings() -> "LedgerQueueSettings":
    from pitch_ledger.infra_adapters.ledger_queue import LedgerQueueSettings

    return LedgerQueueSettings()


def _cosmos_reader(match_id: str) -> "InMemoryLedger":
    """A match's ledger read from Cosmos DB (read only: never emptied, never written)."""
    from pitch_ledger.infra_adapters.cosmos import CosmosLedger

    s = _ledger_settings()
    if not s.cosmos_endpoint:
        raise SystemExit("cosmos ledger needs COSMOS_ENDPOINT")
    ledger = CosmosLedger.connect(s.cosmos_endpoint, s.cosmos_database, s.cosmos_container,
                                  s.key())  # fmt: skip
    ledger.load(match_id)
    return ledger


def cmd_check_models(args: argparse.Namespace) -> int:
    import asyncio

    from pitch_ledger.agents.model_manager import check_models

    checks = asyncio.run(check_models(safety=args.safety))
    for check in checks:
        print(f"[{'PASS' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return 0 if all(c.ok for c in checks) else 1


def _feed_settings(args: argparse.Namespace) -> "FeedSettings":
    from pitch_ledger.infra_adapters.feeds import FeedSettings

    return FeedSettings(feed=args.feed) if args.feed else FeedSettings()


def cmd_stream(args: argparse.Namespace) -> int:
    from pitch_ledger.feed import stream_match
    from pitch_ledger.infra_adapters.feeds import open_publisher

    settings = _feed_settings(args)
    print(f"streaming {args.match_dir} -> {settings.describe()} at {args.speed}x match pace")
    try:
        publisher = open_publisher(settings)
    except ValueError as exc:
        print(f"feed settings: {exc}", file=sys.stderr)
        return 2
    try:
        report = stream_match(
            Path(args.match_dir),
            publisher,
            speed=args.speed,
            start_min=args.start_min,
            minutes=args.minutes,
            with_tracking=not args.no_tracking,
            fault_at=args.fault_at,
        )
    except Exception as exc:  # provider or network error: report it plainly
        print(f"stream failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        publisher.close()
    print(
        f"{report.match_id}: {report.events} events, {report.tracking} tracking rows in "
        f"{report.batches} batches, {report.wall_s:.1f} s (max behind {report.max_behind_s:.1f} s)"
    )
    if report.fault:
        print(f"staged fault: {report.fault}")
    return 0


def cmd_listen(args: argparse.Namespace) -> int:
    import json

    from pitch_ledger.feed import Delivered, listen
    from pitch_ledger.infra_adapters.feeds import open_subscriber

    settings = _feed_settings(args)
    try:
        subscriber = open_subscriber(settings, from_start=args.from_start)
    except ValueError as exc:
        print(f"feed settings: {exc}", file=sys.stderr)
        return 2
    ready = getattr(subscriber, "ready", None)
    if ready is not None and not ready.wait(60):
        error = getattr(subscriber, "last_error", None) or "no partition opened within 60 s"
        print(f"not connected yet: {error}", file=sys.stderr)
    print(f"listening on {settings.describe()}; stops at full time or after {args.idle:g} s idle")
    seen = 0
    store, meter = None, None
    if args.store:
        import time

        from pitch_ledger.eventstore.rows import FeedHealthMeter
        from pitch_ledger.infra_adapters.stores import open_store

        store_settings = _store_settings(args.store)
        store, meter = open_store(store_settings, fail_open=True), FeedHealthMeter()
        print(f"  also writing rows and feed health to {store_settings.describe()}")

    def progress(d: Delivered) -> None:
        nonlocal seen
        seen += 1
        if seen % 1000 == 0:
            print(f"  {seen} messages")
        if store is not None and meter is not None and d.message.kind != "match":
            table = "Events" if d.message.kind == "event" else "Tracking"
            store.append(table, [d.message.row])
            meter.add(d.message.match_id, d.received_at, d.latency_ms)
            if finished := meter.take(before=time.time()):
                store.append("FeedHealth", finished)

    out = Path(args.out)
    try:
        report = listen(
            subscriber, out, args.idle, stop_at_full_time=not args.keep_going, on_message=progress
        )
    finally:
        subscriber.close()
        if store is not None and meter is not None:
            store.append("FeedHealth", meter.take())
            store.close()
    summary = report.as_dict()
    out.mkdir(parents=True, exist_ok=True)
    (out / "listen.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for label, count in report.received.items():
        print(f"  {label}: {count} rows -> {out / label.split('/')[0]}")
    for kind, lat in summary["latency"].items():
        if lat["count"]:
            print(
                f"  latency {kind}: p50 {lat['p50_ms']} ms, p95 {lat['p95_ms']} ms, "
                f"p99 {lat['p99_ms']} ms, max {lat['max_ms']} ms"
            )
    if not report.received:
        error = getattr(subscriber, "last_error", None)
        print("no messages received" + (f"; last error: {error}" if error else ""))
        return 1
    ending = "full time reached" if report.full_time else "stopped"
    print(f"{ending}; stats -> {out / 'listen.json'}")
    return 0


def _store_settings(store: str | None) -> "StoreSettings":
    from pitch_ledger.infra_adapters.stores import StoreSettings

    return StoreSettings(store=store) if store else StoreSettings()


def _history(args: argparse.Namespace, data: Path) -> "HistoryStore":
    from pitch_ledger.infra_adapters.ledger_queue import LedgerQueueSettings, open_history

    settings = LedgerQueueSettings(history=args.history) if args.history else LedgerQueueSettings()
    return open_history(settings, data)


def cmd_history_build(args: argparse.Namespace) -> int:
    """A record for every finished match folder: to the history store and, with --store,
    the event store's season-history tables."""
    import time

    from pitch_ledger.eventstore.rows import history_rows
    from pitch_ledger.history import MatchNotFinished, build_record
    from pitch_ledger.infra_adapters.stores import open_store

    data = Path(args.data)
    folders = sorted(p for p in data.glob("M[0-9][0-9][0-9][0-9]") if (p / MATCH_FILE).is_file())
    if args.match:
        folders = [p for p in folders if p.name in set(args.match)]
    if not folders:
        print(f"no match folders in {data}", file=sys.stderr)
        return 2
    try:
        history = _history(args, data)
        store = open_store(_store_settings(args.store)) if args.store else None
    except ValueError as exc:
        print(f"history/store settings: {exc}", file=sys.stderr)
        return 2
    try:
        for folder in folders:
            files = read_match(folder)
            events = [Event.model_validate(e) for e in files.events]
            tracking = [TrackingSample.model_validate(s) for s in files.tracking]
            try:
                record = build_record(files.match, events, tracking)
            except MatchNotFinished:
                print(f"  {folder.name}: not finished, skipped")
                continue
            history.put(record)
            if store is not None:
                for table, rows in history_rows(record, time.time()).items():
                    store.append(table, rows)
            print(f"  {record.match_id} {record.kickoff_utc:%Y-%m-%d} {record.home_team_id} "
                  f"{record.score_home}-{record.score_away} {record.away_team_id}: "
                  f"{len(record.players)} players, {len(record.goals)} goals")  # fmt: skip
        if store is not None:
            store.flush()
    finally:
        for part in (history, store):
            close = getattr(part, "close", None)
            if close is not None:
                close()
    return 0


def cmd_history_show(args: argparse.Namespace) -> int:
    """The season's results and league table, from the history store."""
    from pitch_ledger.history.table import league_table

    data = Path(args.data)
    try:
        history = _history(args, data)
    except ValueError as exc:
        print(f"history settings: {exc}", file=sys.stderr)
        return 2
    try:
        competition, season = args.competition, args.season
        if competition is None or season is None:
            folder = next((p for p in sorted(data.glob("M[0-9][0-9][0-9][0-9]"))
                           if (p / MATCH_FILE).is_file()), None)  # fmt: skip
            if folder is None:
                print("give --competition and --season (no match folder to read them from)")
                return 2
            match = read_match(folder, with_tracking=False).match
            competition, season = competition or match.competition, season or match.season
        records = history.season(competition, season)
    finally:
        close = getattr(history, "close", None)
        if close is not None:
            close()
    print(f"{competition} {season}: {len(records)} matches")
    for r in records:
        tricks = f" (hat-trick: {', '.join(r.hat_tricks)})" if r.hat_tricks else ""
        print(f"  {r.kickoff_utc:%Y-%m-%d} {r.match_id} {r.home_team_id} {r.score_home}-"
              f"{r.score_away} {r.away_team_id}{tricks}")  # fmt: skip
    print("  team  P  W  D  L  GF  GA  GD  Pts  form")
    for row in league_table(records):
        print(f"  {row.team_id}  {row.played}  {row.won}  {row.drawn}  {row.lost}  "
              f"{row.goals_for:>2}  {row.goals_against:>2}  {row.goal_difference:>+3}  "
              f"{row.points:>3}  {''.join(row.form)}")  # fmt: skip
    return 0


def cmd_store_schema(args: argparse.Namespace) -> int:
    from pitch_ledger.eventstore import create_script

    print(create_script(), end="")
    return 0


def cmd_store(args: argparse.Namespace) -> int:
    from pitch_ledger.eventstore.rows import export_match
    from pitch_ledger.infra_adapters.stores import open_store

    settings = _store_settings(args.store)
    print(f"writing {args.match_dir} -> {settings.describe()}")
    try:
        store = open_store(settings)
    except ValueError as exc:
        print(f"store settings: {exc}", file=sys.stderr)
        return 2
    try:
        counts = export_match(Path(args.match_dir), store, with_tracking=not args.no_tracking)
    except Exception as exc:  # provider or network error: report it plainly
        print(f"store failed: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 1
    finally:
        store.close()
    print("  " + ", ".join(f"{table} {n}" for table, n in counts.items()))
    return 0


def cmd_check_store(args: argparse.Namespace) -> int:
    from pitch_ledger.infra_adapters.adx import check_store

    settings = _store_settings(None)
    checks = check_store(settings.adx_cluster_uri, settings.adx_database)
    for check in checks:
        print(f"[{'PASS' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return 0 if all(c.ok for c in checks) else 1


def cmd_check_ledger(args: argparse.Namespace) -> int:
    from pitch_ledger.infra_adapters.cosmos import check_ledger
    from pitch_ledger.infra_adapters.ledger_queue import LedgerQueueSettings

    s = LedgerQueueSettings()
    checks = check_ledger(s.cosmos_endpoint, s.cosmos_database, s.cosmos_container, s.key())
    for check in checks:
        print(f"[{'PASS' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return 0 if all(c.ok for c in checks) else 1


def cmd_check_queue(args: argparse.Namespace) -> int:
    from pitch_ledger.infra_adapters.ledger_queue import LedgerQueueSettings
    from pitch_ledger.infra_adapters.servicebus import check_queue

    s = LedgerQueueSettings()
    checks = check_queue(
        s.servicebus_namespace, s.servicebus_queue, s.connection(), s.servicebus_transport
    )
    for check in checks:
        print(f"[{'PASS' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return 0 if all(c.ok for c in checks) else 1


def cmd_live(args: argparse.Namespace) -> int:
    import asyncio
    import json

    from pitch_ledger.infra_adapters.cosmos import CosmosLedger
    from pitch_ledger.infra_adapters.feeds import open_subscriber
    from pitch_ledger.infra_adapters.ledger_queue import (
        LedgerQueueSettings,
        describe_history,
        open_history,
        open_queue,
    )
    from pitch_ledger.infra_adapters.stores import open_store
    from pitch_ledger.ledger import FileLedger, InMemoryLedger
    from pitch_ledger.live.service import LiveOptions, LiveService

    _start_tracing("pitch-ledger-live")
    feed = _feed_settings(args)
    overrides = {k: v for k, v in (("ledger", args.ledger), ("queue", args.queue),
                                   ("history", args.history)) if v and v != "none"}  # fmt: skip
    services = LedgerQueueSettings(**overrides)
    out = Path(args.out)
    try:
        subscriber = open_subscriber(feed, from_start=args.from_start)
        trigger_queue = open_queue(services)
    except ValueError as exc:
        print(f"settings: {exc}", file=sys.stderr)
        return 2
    ledger: InMemoryLedger
    if services.ledger == "cosmos":
        if not services.cosmos_endpoint:
            print("settings: cosmos ledger needs COSMOS_ENDPOINT", file=sys.stderr)
            return 2
        ledger = CosmosLedger.connect(
            services.cosmos_endpoint,
            services.cosmos_database,
            services.cosmos_container,
            services.key(),
            fresh_all=args.fresh,  # a new live run replaces old documents per match
        )
    else:
        ledger = FileLedger(out / "ledger", fresh=args.fresh)
    store = open_store(_store_settings(args.store), fail_open=True) if args.store else None
    try:
        history = None if args.history == "none" else open_history(services, out.parent)
    except ValueError as exc:
        print(f"settings: {exc}", file=sys.stderr)
        return 2
    only = tuple(x.strip() for x in args.only.split(",") if x.strip()) if args.only else None
    options = LiveOptions(
        approve=args.approve, inject_first_momentum=args.inject_error, provider=args.provider,
        budget=args.budget, only=only, settle_s=args.settle, tick_s=args.tick, idle_s=args.idle,
    )  # fmt: skip
    print(
        f"live: feed {feed.describe()}; queue {services.describe_queue()}; ledger "
        f"{services.describe_ledger()}; store {args.store or 'off'}; history "
        f"{describe_history(services) if history is not None else 'off'}; out {out}"
    )
    from pitch_ledger.infra_adapters.webpubsub import PushSettings, open_notifier

    push = PushSettings()
    print(f"  push: {push.describe()}")
    service = LiveService(subscriber, trigger_queue, ledger, store, out, options,
                          notifier=open_notifier(push), history=history)  # fmt: skip
    try:
        report = asyncio.run(service.run())
    finally:
        for part in (subscriber, trigger_queue, store, ledger, history):
            close = getattr(part, "close", None)
            if close is not None:
                close()
    summary = report.as_dict()
    out.mkdir(parents=True, exist_ok=True)
    (out / "live.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", "utf-8")
    for match_id, counts in summary["matches"].items():
        print(f"  {match_id}: {counts['events']} events, {counts['tracking']} tracking rows, "
              f"{counts['triggers']} triggers")  # fmt: skip
    for outcome in report.outcomes:
        if outcome.insight_id:
            insight = ledger.get_insight(outcome.insight_id)
            headline = insight.headline if insight else ""
            print(f"  {outcome.insight_id} {outcome.status:<7} {outcome.audit_status or '':<9} "
                  f"{outcome.overlays:>2} overlays  {headline}")  # fmt: skip
    u, w = summary["usage"], summary["trigger_to_outcome_s"]
    print(f"processed {summary['processed']} triggers {summary['status']}; trigger to outcome "
          f"p50 {w['p50']} s, max {w['max']} s; model calls {u.get('calls', 0)}; "
          f"failures {len(summary['failures'])}")  # fmt: skip
    return 0 if not summary["failures"] else 1


def cmd_check_push(args: argparse.Namespace) -> int:
    from pitch_ledger.infra_adapters.webpubsub import PushSettings, check_push

    checks = check_push(PushSettings())
    for check in checks:
        print(f"[{'PASS' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return 0 if all(c.ok for c in checks) else 1


def cmd_check_tracing(args: argparse.Namespace) -> int:
    from pitch_ledger.infra_adapters.telemetry import TelemetrySettings, check_tracing

    checks = check_tracing(TelemetrySettings())
    for check in checks:
        print(f"[{'PASS' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return 0 if all(c.ok for c in checks) else 1


def cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    from pitch_ledger.api.app import create_app

    _start_tracing("pitch-ledger-api")  # producer decisions run the agents
    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pitch-ledger", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="simulate a synthetic match and write its files")
    gen.add_argument("--scenario", default="none", help="planted scenario ID (default none)")
    gen.add_argument("--seed", type=int, default=4211, help="random seed (default 4211)")
    gen.add_argument("--out", required=True, help="output directory, e.g. data/M0001")
    gen.add_argument("--match-id", help="match ID (default: output folder name or M0001)")
    gen.add_argument("--home", default="T01", help="home team ID (default T01)")
    gen.add_argument("--away", default="T02", help="away team ID (default T02)")
    gen.add_argument("--kickoff", help="kick-off time, ISO 8601 with time zone")
    gen.set_defaults(func=cmd_generate)

    every = sub.add_parser(
        "generate-all", help="generate the demo matches in scenarios/matches.yaml"
    )
    every.add_argument("--out", default="data", help="root folder (default data)")
    every.set_defaults(func=cmd_generate_all)

    val = sub.add_parser("validate", help="run every validation rule over a match directory")
    val.add_argument("match_dir")
    val.add_argument("--no-tracking", action="store_true", help="skip tracking rows")
    val.add_argument("--no-realism", action="store_true", help="skip realism bands (MATCH-02)")
    val.add_argument("--show", type=int, default=20, help="how many results to print")
    val.set_defaults(func=cmd_validate)

    real = sub.add_parser("realism", help="compare a match with the realism bands")
    real.add_argument("match_dir")
    real.add_argument("--strict", action="store_true", help="exit 1 if any metric is out")
    real.set_defaults(func=cmd_realism)

    det = sub.add_parser("detect", help="run the moment detector and check the scenario")
    det.add_argument("match_dir")
    det.add_argument("--no-tracking", action="store_true", help="skip tracking-based rules")
    det.add_argument("--all", action="store_true", help="list every trigger, not just key ones")
    det.set_defaults(func=cmd_detect)

    mcp = sub.add_parser("mcp", help="serve the read-only stats tools over MCP (stdio)")
    mcp.add_argument("match_dir")
    mcp.set_defaults(func=cmd_mcp)

    rep = sub.add_parser(
        "replay", help="run the agents over a match: audited insights, overlays, ledger"
    )
    rep.add_argument("match_dir")
    rep.add_argument("--approve", action="store_true", help="approve as producer_demo")
    rep.add_argument(
        "--inject-error",
        action="store_true",
        help="plant a wrong number in the first momentum draft (the Auditor must catch it)",
    )
    rep.add_argument(
        "--provider", choices=["offline", "foundry"], help="model provider (default: settings)"
    )
    rep.add_argument("--local-stats", action="store_true", help="stats in-process, not MCP")
    rep.add_argument("--only", help="comma-separated insight types or rule IDs to process")
    rep.add_argument("--budget", type=int, help="model call budget for paid providers")
    rep.add_argument("--ledger", choices=["file", "cosmos"], help="ledger (default: settings)")
    rep.add_argument("--queue", choices=["memory", "servicebus"], help="trigger queue")
    rep.add_argument(
        "--history",
        choices=["file", "cosmos", "none"],
        help="match records for season facts, and where this match's record goes "
        "(default: settings; none: no season facts)",
    )
    rep.set_defaults(func=cmd_replay)

    ev = sub.add_parser("evaluate", help="scorecard: detection, accuracy, planted errors, coverage")
    ev.add_argument("--out", default="data/scorecard.json", help="JSON output path")
    ev.add_argument("--sweep", type=int, default=5, help="seeds per demo match in the sweep")
    ev.add_argument("--provider", choices=["offline", "foundry"], help="model provider")
    ev.add_argument("--match", action="append", help="limit to these demo match IDs")
    ev.add_argument(
        "--store", choices=["file", "adx"], help="also keep the run in the EvalRuns table"
    )
    ev.set_defaults(func=cmd_evaluate)

    his = sub.add_parser("history", help="season history: match records and the league table")
    his_sub = his.add_subparsers(dest="history_command", required=True)
    hb = his_sub.add_parser("build", help="a record for every finished match folder")
    hb.add_argument("--data", default="data", help="folder of match folders")
    hb.add_argument("--match", action="append", help="limit to these match IDs")
    hb.add_argument("--history", choices=["file", "cosmos"], help="history store")
    hb.add_argument("--store", choices=["file", "adx"], help="also write the event-store tables")
    hb.set_defaults(func=cmd_history_build)
    hs = his_sub.add_parser("show", help="the season's results and league table")
    hs.add_argument("--data", default="data", help="folder of match folders")
    hs.add_argument("--history", choices=["file", "cosmos"], help="history store")
    hs.add_argument("--competition", help="default: from the first match folder")
    hs.add_argument("--season", help="default: from the first match folder, e.g. 2026-27")
    hs.set_defaults(func=cmd_history_show)

    fe = sub.add_parser(
        "foundry-eval",
        help="evaluate every aired insight in Foundry: code checks, groundedness, coherence",
    )
    fe.add_argument("--match", action="append", help="demo match IDs (default: all six)")
    fe.add_argument("--ledger", choices=["file", "cosmos"], help="ledger (default: settings)")
    fe.add_argument(
        "--history", choices=["file", "cosmos"], help="match records (default: as the ledger)"
    )
    fe.add_argument("--data", default="data", help="file ledgers under DATA/<match>/ledger")
    fe.add_argument(
        "--scorecard",
        default="data/scorecard.json",
        help="attach this scorecard's gates (from `evaluate`)",
    )
    fe.add_argument("--name", default="insights", help="run name in Foundry")
    fe.add_argument("--no-judges", action="store_true", help="code checks only (no model)")
    fe.add_argument("--safety", action="store_true", help="add the safety evaluators")
    fe.add_argument("--dry-run", action="store_true", help="build the rows only; no upload")
    fe.set_defaults(func=cmd_foundry_eval)

    chk = sub.add_parser(
        "check-models", help="check model settings, Entra ID sign-in and each deployment"
    )
    chk.add_argument(
        "--safety",
        action="store_true",
        help="also send a prompt-injection probe the content filter must block",
    )
    chk.set_defaults(func=cmd_check_models)

    feeds = ["file", "eventhubs"]
    st = sub.add_parser("stream", help="publish a match to the live feed at match pace")
    st.add_argument("match_dir")
    st.add_argument("--speed", type=float, default=1.0, help="x real time (default 1)")
    st.add_argument("--start-min", type=float, default=0.0, help="first match minute")
    st.add_argument("--minutes", type=float, help="how many match minutes (default: to the end)")
    st.add_argument("--no-tracking", action="store_true", help="events only")
    st.add_argument("--feed", choices=feeds, help="feed adapter (default: settings, file)")
    st.add_argument(
        "--fault-at", type=float, metavar="MIN",
        help="demo: send one bad row at this match minute, then pause 12 s",
    )  # fmt: skip
    st.set_defaults(func=cmd_stream)

    ls = sub.add_parser("listen", help="read the live feed, land rows, report feed latency")
    ls.add_argument("--out", default="data/live", help="landing folder (default data/live)")
    ls.add_argument("--idle", type=float, default=30.0, help="stop after this many idle seconds")
    ls.add_argument("--from-start", action="store_true", help="read retained messages too")
    ls.add_argument("--keep-going", action="store_true", help="do not stop at full time")
    ls.add_argument("--feed", choices=feeds, help="feed adapter (default: settings, file)")
    ls.add_argument("--store", choices=["file", "adx"], help="also write rows and feed health")
    ls.set_defaults(func=cmd_listen)

    sch = sub.add_parser("store-schema", help="print the KQL script that creates the tables")
    sch.set_defaults(func=cmd_store_schema)

    sto = sub.add_parser("store", help="write a match to the event store (file or ADX)")
    sto.add_argument("match_dir")
    sto.add_argument("--store", choices=["file", "adx"], help="adapter (default: settings, file)")
    sto.add_argument("--no-tracking", action="store_true", help="skip tracking rows")
    sto.set_defaults(func=cmd_store)

    cks = sub.add_parser("check-store", help="check ADX settings, sign-in, roles, tables, a write")
    cks.set_defaults(func=cmd_check_store)

    ckl = sub.add_parser("check-ledger", help="check Cosmos DB settings, sign-in and a write")
    ckl.set_defaults(func=cmd_check_ledger)

    ckq = sub.add_parser("check-queue", help="check Service Bus settings, sign-in and a probe")
    ckq.set_defaults(func=cmd_check_queue)

    lv = sub.add_parser("live", help="live service: feed -> engine -> queue -> agents -> ledger")
    lv.add_argument("--feed", choices=feeds, help="feed adapter (default: settings)")
    lv.add_argument("--queue", choices=["memory", "servicebus"], help="trigger queue")
    lv.add_argument("--ledger", choices=["file", "cosmos"], help="ledger (default: settings)")
    lv.add_argument("--store", choices=["file", "adx"], help="also write the event store")
    lv.add_argument(
        "--history",
        choices=["file", "cosmos", "none"],
        help="match records: season facts, and each match's record at full time",
    )
    lv.add_argument("--approve", action="store_true", help="approve as producer_demo")
    lv.add_argument("--inject-error", action="store_true", help="plant an error (first momentum)")
    lv.add_argument("--provider", choices=["offline", "foundry"], help="model provider")
    lv.add_argument("--budget", type=int, help="model call budget for paid providers")
    lv.add_argument("--only", help="comma-separated insight types or rule IDs to process")
    lv.add_argument("--out", default="data/live", help="landing folder (default data/live)")
    lv.add_argument("--fresh", action="store_true", help="replace earlier ledger records")
    lv.add_argument("--settle", type=float, default=60.0, help="match seconds before emitting")
    lv.add_argument("--tick", type=float, default=5.0, help="seconds between detector runs")
    lv.add_argument("--idle", type=float, default=30.0, help="stop after this many idle seconds")
    lv.add_argument("--from-start", action="store_true", help="read retained feed messages too")
    lv.set_defaults(func=cmd_live)

    ckp = sub.add_parser("check-push", help="check Web PubSub settings, sign-in and a send")
    ckp.set_defaults(func=cmd_check_push)

    ckt = sub.add_parser("check-tracing", help="check tracing settings, sign-in and a test span")
    ckt.set_defaults(func=cmd_check_tracing)

    api = sub.add_parser("api", help="serve the /api/v1 API (Uvicorn)")
    api.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    api.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    api.set_defaults(func=cmd_api)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
