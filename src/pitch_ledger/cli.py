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
from pitch_ledger.matchlab.io import read_match, write_match, write_triggers
from pitch_ledger.matchlab.scenarios import load_manifest, load_scenario
from pitch_ledger.schema import Event, TrackingSample, Trigger
from pitch_ledger.schema.enums import ScenarioId
from pitch_ledger.validation import RULES, measure, validate_match
from pitch_ledger.validation.realism import RealismCheck

if TYPE_CHECKING:
    from pitch_ledger.infra_adapters.feeds import FeedSettings
    from pitch_ledger.infra_adapters.stores import StoreSettings

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
        open_ledger,
        open_queue,
    )

    overrides = {k: v for k, v in (("ledger", args.ledger), ("queue", args.queue)) if v}
    services = LedgerQueueSettings(**overrides)
    match_id = read_match(Path(args.match_dir), with_tracking=False).match.match_id
    try:
        cloud_ledger = open_ledger(services, fresh_match=match_id)
        queue = open_queue(services)
    except ValueError as exc:
        print(f"ledger/queue settings: {exc}", file=sys.stderr)
        return 2
    if services.ledger != "file" or services.queue != "memory":
        print(f"ledger: {services.describe_ledger()}; queue: {services.describe_queue()}")
    try:
        run = asyncio.run(replay_match(Path(args.match_dir), options, cloud_ledger, queue))
    finally:
        for part in (cloud_ledger, queue):
            close = getattr(part, "close", None)
            if close is not None:
                close()
    summary, ledger = run.summary, run.ledger
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
    from pitch_ledger.infra_adapters.ledger_queue import LedgerQueueSettings, open_queue
    from pitch_ledger.infra_adapters.stores import open_store
    from pitch_ledger.ledger import FileLedger, InMemoryLedger
    from pitch_ledger.live.service import LiveOptions, LiveService

    _start_tracing("pitch-ledger-live")
    feed = _feed_settings(args)
    overrides = {k: v for k, v in (("ledger", args.ledger), ("queue", args.queue)) if v}
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
    only = tuple(x.strip() for x in args.only.split(",") if x.strip()) if args.only else None
    options = LiveOptions(
        approve=args.approve, inject_first_momentum=args.inject_error, provider=args.provider,
        budget=args.budget, only=only, settle_s=args.settle, tick_s=args.tick, idle_s=args.idle,
    )  # fmt: skip
    print(
        f"live: feed {feed.describe()}; queue {services.describe_queue()}; ledger "
        f"{services.describe_ledger()}; store {args.store or 'off'}; out {out}"
    )
    from pitch_ledger.infra_adapters.webpubsub import PushSettings, open_notifier

    push = PushSettings()
    print(f"  push: {push.describe()}")
    service = LiveService(
        subscriber, trigger_queue, ledger, store, out, options, notifier=open_notifier(push)
    )
    try:
        report = asyncio.run(service.run())
    finally:
        for part in (subscriber, trigger_queue, store, ledger):
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
    rep.set_defaults(func=cmd_replay)

    ev = sub.add_parser("evaluate", help="scorecard: detection, accuracy, planted errors, coverage")
    ev.add_argument("--out", default="data/scorecard.json", help="JSON output path")
    ev.add_argument("--sweep", type=int, default=5, help="seeds per demo match in the sweep")
    ev.add_argument("--provider", choices=["offline", "foundry"], help="model provider")
    ev.add_argument("--match", action="append", help="limit to these demo match IDs")
    ev.set_defaults(func=cmd_evaluate)

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
